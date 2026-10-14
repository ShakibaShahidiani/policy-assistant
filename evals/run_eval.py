"""
Evaluation harness for this RAG prototype.

Runs the golden set through the REAL pipeline (same functions the API uses),
isolates each case in its own conversation, and computes the five metrics
defined in golden_set.py:

  1. Status Resolution Accuracy   (expected_status)
  2. Retrieval Recall             (must_cite)
  3. Fact Coverage                (required_facts / forbidden_facts)
  4. Faithfulness  (LLM judge)    (reference_answer)
  5. Guardrail Accuracy           (expect_refusal)

It drives the pipeline IN-PROCESS rather than over HTTP so it can inspect the
actual retrieved chunks (Metric 2) — /chat only returns citations, which is
"what was cited", not "what was retrieved".

Run from the project root (server does NOT need to be running):

    python evals/run_eval.py

Writes evals/results.json for the UI to display.
"""

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone

# --- make project-root modules importable when run as `python evals/run_eval.py` ---
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import aiosqlite

from db import (DB_PATH, get_or_create_conversation, init_db, save_document)
from ingest import (build_chunk_meta, chunk_pages, client, col, embed_chunks,
                    embed_query, extract_metadata, parse_pdf,
                    resolve_upload_status, retrieve)
from main import SYSTEM_PROMPT
from golden_set import GOLDEN_SET

NO_CONTEXT = "I cannot find relevant information on this topic in the uploaded documents."
FAITHFULNESS_PASS = 0.7   # judge score at/above which a case's grounding passes

JUDGE_SYSTEM = """You are a strict evaluator for an internal policy QA assistant.
You are given a QUESTION, the CONTEXT excerpts the assistant was shown, the
assistant's ANSWER, and a REFERENCE answer written by an expert.

Score two things from 0.0 to 1.0:

- "faithfulness": Is every factual claim in the ANSWER supported by the CONTEXT?
  1.0 = every claim is grounded in the context. Lower it for any claim that is
  not present in the context (hallucination), even if the claim is true.

- "correctness": Does the ANSWER convey the same key facts as the REFERENCE?
  1.0 = all key facts present and consistent. Lower it for missing or wrong facts.

Return ONLY JSON: {"faithfulness": <float>, "correctness": <float>, "notes": "<short>"}"""


# ----------------------------------------------------------------------------------
# Pipeline drivers (mirror main.py's /upload and /chat orchestration)
# ----------------------------------------------------------------------------------

async def reset_conversation(conv_id: str):
    """Make a case idempotent: wipe any prior eval data for this conversation."""
    try:
        col.delete(where={"conversation_id": {"$eq": conv_id}})
    except Exception:
        pass
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM documents WHERE conversation_id=?", (conv_id,))
        await db.execute("DELETE FROM messages WHERE conversation_id=?", (conv_id,))
        await db.commit()
    await get_or_create_conversation(conv_id)


async def ingest_one(conv_id: str, filename: str) -> dict:
    """Replicates the /upload pipeline for one PDF. Returns resolved metadata."""
    path = os.path.join(ROOT, "pdfs", filename)
    pages = parse_pdf(path)
    meta = await extract_metadata(pages[0]["text"])
    meta["filename"] = filename
    meta = await resolve_upload_status(conv_id, meta, col)

    chunks = chunk_pages(pages, meta)
    embeddings = await embed_chunks([c["text"] for c in chunks])
    ids = [str(uuid.uuid4()) for _ in chunks]
    docs = [c["text"] for c in chunks]
    metas = [build_chunk_meta(c, conv_id, filename) for c in chunks]
    col.add(ids=ids, embeddings=embeddings, documents=docs, metadatas=metas)

    await save_document(filename, conv_id, meta)
    return meta


async def ask(conv_id: str, question: str):
    """Replicates the /chat pipeline (single turn, no history). Returns
    (retrieved_chunks, answer, context_str)."""
    query_vec = await embed_query(question)
    chunks = retrieve(query_vec, conv_id)

    if not chunks:
        return [], NO_CONTEXT, ""

    context_blocks = []
    for i, (doc, md) in enumerate(chunks, start=1):
        context_blocks.append(f"[{i}] {md.get('filename')}, p.{md.get('page_number')}:\n{doc}")
    context_str = "\n\n".join(context_blocks)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"{context_str}\n\nQuestion: {question}"},
    ]
    completion = await client.chat.completions.create(model="gpt-4o-mini", messages=messages)
    return chunks, completion.choices[0].message.content, context_str


async def judge(question, context_str, answer, reference) -> dict:
    user = (f"QUESTION:\n{question}\n\nCONTEXT:\n{context_str}\n\n"
            f"ANSWER:\n{answer}\n\nREFERENCE:\n{reference}")
    resp = await client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": JUDGE_SYSTEM},
                  {"role": "user", "content": user}],
    )
    return json.loads(resp.choices[0].message.content)


# ----------------------------------------------------------------------------------
# Per-case grading
# ----------------------------------------------------------------------------------

def is_refusal(answer: str) -> bool:
    return "cannot find relevant information" in answer.lower()


async def final_statuses(conv_id: str) -> dict:
    """Read each doc's FINAL status after all uploads (supersession happens when a
    later upload arrives, so a doc's status at its own upload moment is not final)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT filename, status FROM documents WHERE conversation_id=?", (conv_id,)
        ) as cur:
            rows = await cur.fetchall()
    return {r["filename"]: r["status"] for r in rows}


def status_ok(actual, expected) -> bool:
    """expected may be a single status or a list of acceptable ones (e.g. a doc
    that self-describes as both 'temporary' and 'interim')."""
    if isinstance(expected, (list, tuple, set)):
        return actual in expected
    return actual == expected


def expected_is_active(expected) -> bool:
    if isinstance(expected, (list, tuple, set)):
        return "active" in expected
    return expected == "active"


def fact_present(fact, answer_lower: str) -> bool:
    """A fact may be a string (must appear) or a list of synonyms (any-of)."""
    if isinstance(fact, (list, tuple)):
        return any(alt.lower() in answer_lower for alt in fact)
    return fact.lower() in answer_lower


async def run_case(case: dict) -> dict:
    conv_id = f"eval-{case['id']}"
    await reset_conversation(conv_id)

    # --- uploads + Metric 1 (status resolution) ---
    for fn in case["uploads"]:
        await ingest_one(conv_id, fn)
    status_results = await final_statuses(conv_id)   # FINAL status, after all uploads

    expected_status = case["expected_status"]
    status_total = len(expected_status)
    status_correct = sum(
        1 for fn, exp in expected_status.items() if status_ok(status_results.get(fn), exp)
    )
    status_mismatches = {
        fn: {"expected": exp, "actual": status_results.get(fn)}
        for fn, exp in expected_status.items() if not status_ok(status_results.get(fn), exp)
    }

    # --- ask the question ---
    retrieved, answer, context_str = await ask(conv_id, case["question"])
    retrieved_files = {md.get("filename") for _, md in retrieved}

    # --- Metric 2 (retrieval recall) ---
    must = case["must_cite"]
    recall_hits = sum(1 for d in must if d in retrieved_files)
    recall = (recall_hits / len(must)) if must else None

    # --- leakage invariant (should always be empty given the pre-filter) ---
    forbidden_sources = [fn for fn, st in expected_status.items() if not expected_is_active(st)]
    leaked = [d for d in forbidden_sources if d in retrieved_files]

    # --- Metric 3 (fact coverage) ---
    al = answer.lower()
    req = case["required_facts"]
    req_hits = sum(1 for f in req if fact_present(f, al))
    facts = (req_hits / len(req)) if req else None
    forbidden_viol = [f for f in case["forbidden_facts"] if fact_present(f, al)]

    # --- Metric 5 (guardrail) ---
    refused = is_refusal(answer)
    guardrail_ok = (refused == case["expect_refusal"])

    # --- Metric 4 (faithfulness) — skip for refusal cases ---
    faith = corr = None
    judge_notes = None
    if not case["expect_refusal"]:
        try:
            j = await judge(case["question"], context_str, answer, case["reference_answer"])
            faith = float(j.get("faithfulness"))
            corr = float(j.get("correctness"))
            judge_notes = j.get("notes")
        except Exception as exc:
            judge_notes = f"judge error: {exc}"

    # --- overall pass/fail ---
    passed = True
    if status_total:
        passed &= (status_correct == status_total)
    if recall is not None:
        passed &= (recall == 1.0)
    if facts is not None:
        passed &= (facts == 1.0)
    passed &= (len(forbidden_viol) == 0)
    passed &= guardrail_ok
    passed &= (len(leaked) == 0)
    if faith is not None:
        passed &= (faith >= FAITHFULNESS_PASS)

    return {
        "id": case["id"],
        "what_it_tests": case.get("what_it_tests", ""),
        "limitation": case.get("limitation"),
        "status_correct": status_correct, "status_total": status_total,
        "status_mismatches": status_mismatches,
        "recall": recall, "recall_hits": recall_hits, "recall_total": len(must),
        "facts": facts, "req_hits": req_hits, "req_total": len(req),
        "forbidden_viol": forbidden_viol,
        "expect_refusal": case["expect_refusal"], "refused": refused,
        "guardrail_ok": guardrail_ok,
        "faithfulness": faith, "correctness": corr, "judge_notes": judge_notes,
        "leaked": leaked,
        "answer": answer,
        "passed": bool(passed),
    }


# ----------------------------------------------------------------------------------
# Reporting
# ----------------------------------------------------------------------------------

def _cell(value, kind):
    if value is None:
        return "  —  "
    if kind == "ratio":
        return f"{value[0]}/{value[1]}"
    if kind == "pct":
        return f"{round(value * 100)}%"
    if kind == "score":
        return f"{value:.2f}"
    if kind == "bool":
        return " ✓ " if value else " ✗ "
    return str(value)


def print_table(results):
    print("\n" + "=" * 92)
    print("PER-CASE RESULTS")
    print("=" * 92)
    header = f"{'CASE':36} {'STATUS':>7} {'RECALL':>7} {'FACTS':>6} {'REFUSE':>7} {'FAITH':>6}  RESULT"
    print(header)
    print("-" * 92)
    for r in results:
        status = _cell((r["status_correct"], r["status_total"]), "ratio") if r["status_total"] else "  —  "
        recall = _cell((r["recall_hits"], r["recall_total"]), "ratio") if r["recall_total"] else "  —  "
        facts = _cell((r["req_hits"], r["req_total"]), "ratio") if r["req_total"] else "  —  "
        refuse = _cell(r["guardrail_ok"], "bool")
        faith = _cell(r["faithfulness"], "score")
        result = "PASS" if r["passed"] else "FAIL"
        print(f"{r['id']:36} {status:>7} {recall:>7} {facts:>6} {refuse:>7} {faith:>6}  {result}")
    print("-" * 92)


def print_summary(results):
    n = len(results)
    passed = sum(1 for r in results if r["passed"])

    sc = sum(r["status_correct"] for r in results)
    st = sum(r["status_total"] for r in results)

    recall_cases = [r for r in results if r["recall_total"]]
    recall_avg = sum(r["recall"] for r in recall_cases) / len(recall_cases) if recall_cases else None

    req_hits = sum(r["req_hits"] for r in results)
    req_total = sum(r["req_total"] for r in results)

    faith_cases = [r for r in results if r["faithfulness"] is not None]
    faith_avg = sum(r["faithfulness"] for r in faith_cases) / len(faith_cases) if faith_cases else None

    refusal_cases = [r for r in results if r["expect_refusal"]]
    refusal_ok = sum(1 for r in refusal_cases if r["guardrail_ok"])

    leak_total = sum(len(r["leaked"]) for r in results)

    print("\n" + "=" * 50)
    print("  POLICY ASSISTANT — EVALUATION SCORECARD")
    print("=" * 50)
    print(f"  Cases passed            {passed}/{n}   ({round(passed/n*100)}%)")
    print()
    print("  Metric 1  Status resolution   {}/{}   ({}%)".format(sc, st, round(sc/st*100) if st else 0))
    print("  Metric 2  Retrieval recall    avg {}".format(_cell(recall_avg, 'pct') if recall_avg is not None else '—'))
    print("  Metric 3  Fact coverage       {}/{}".format(req_hits, req_total))
    print("  Metric 4  Faithfulness        avg {}".format(_cell(faith_avg, 'score') if faith_avg is not None else '—'))
    print("  Metric 5  Guardrail refusals  {}/{}".format(refusal_ok, len(refusal_cases)))
    print()
    print("  Invariant Version leakage     {} stale chunks retrieved".format(leak_total))
    print("=" * 50)

    # Honest callout of any failing case
    fails = [r for r in results if not r["passed"]]
    if fails:
        print("\n  Cases needing attention:")
        for r in fails:
            reasons = []
            if r["status_total"] and r["status_correct"] != r["status_total"]:
                reasons.append(f"status {r['status_mismatches']}")
            if r["recall_total"] and r["recall"] != 1.0:
                reasons.append(f"recall {r['recall_hits']}/{r['recall_total']}")
            if r["req_total"] and r["facts"] != 1.0:
                reasons.append(f"facts {r['req_hits']}/{r['req_total']}")
            if r["forbidden_viol"]:
                reasons.append(f"forbidden {r['forbidden_viol']}")
            if not r["guardrail_ok"]:
                reasons.append(f"refusal expected={r['expect_refusal']} got={r['refused']}")
            if r["faithfulness"] is not None and r["faithfulness"] < FAITHFULNESS_PASS:
                reasons.append(f"faithfulness {r['faithfulness']:.2f}")
            if r["leaked"]:
                reasons.append(f"LEAKAGE {r['leaked']}")
            note = "  (expected — known limitation)" if r["limitation"] else ""
            print(f"   - {r['id']}: {', '.join(reasons)}{note}")
    print()


def save_json(results):
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cases": results,
    }
    out = os.path.join(ROOT, "evals", "results.json")
    with open(out, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"  Results written to {out}\n")


# ----------------------------------------------------------------------------------

async def main():
    await init_db()
    results = []
    for case in GOLDEN_SET:
        print(f"  running {case['id']} ...")
        results.append(await run_case(case))
    print_table(results)
    print_summary(results)
    save_json(results)


if __name__ == "__main__":
    asyncio.run(main())
