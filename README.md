# Policy Assistant

A minimal Python REST API demonstrating Retrieval-Augmented Generation (RAG) over
company policy PDFs — built as a case-study prototype for an organization with a
large, frequently revised internal policy library. Two endpoints:
`POST /upload` (ingest a PDF) and `POST /chat` (answer a question with citations).

**The hard part here isn't retrieval — it's version resolution.** Any company
with a mature policy library ends up with superseded versions, interim memos,
and temporary pandemic-era exceptions still floating around. A naive RAG
pipeline will happily retrieve and cite a policy that's been dead for three
years. This prototype solves that with a metadata layer that tracks document
status *before* anything reaches the LLM, plus a system prompt that handles
the residual conflicts a metadata filter can't.

## The problem, concretely

Upload two versions of the same expense policy — a 2022 one and a 2025 one —
and ask "what's the lodging limit?" A naive pipeline retrieves chunks from
both by embedding similarity and lets the LLM guess which number is current.
This pipeline:

1. Extracts `policy_id`, `version`, `effective_date`, and a self-described
   status (`active` / `interim` / `temporary` / `historical`) from every
   document at upload time.
2. Compares the new document against any existing active document sharing
   the same `policy_id` in the conversation, and marks the older one
   `superseded` — in both the vector store and the relational store.
3. Filters retrieval on `status == "active"` as a hard pre-condition, so a
   superseded or self-described stale document can never reach the LLM,
   regardless of how well it embeds against the query.
4. Falls back to a system prompt (see [`SYSTEM_PROMPT.md`](SYSTEM_PROMPT.md))
   for the residual case the filter can't resolve alone: two *different*,
   both-active policies that overlap on the same topic (recency governs, but
   the older one still contributes any non-redundant obligation).

Sample documents and a 9-case golden eval set are included so this is
runnable and checkable end-to-end — see [Demo](#demo) and
[Evaluation](#evaluation) below.

## Architecture

```
            ┌─────────────┐        ┌──────────────────┐
  PDF ──►   │  PyMuPDF    │ ──►    │  OpenAI JSON mode │ ──► metadata
            │  (local     │        │  (first page only)│     {policy_id, version,
            │  parsing)   │        └──────────────────┘      effective_date, status}
            └─────────────┘                 │
                   │                         ▼
                   │                 resolve_upload_status()
                   │                 (supersede older active doc
                   │                  with same policy_id, if any)
                   ▼                         │
         section-aware chunking              ▼
                   │                 SQLite (documents, conversations,
                   ▼                  messages — audit trail)
         batch embed (OpenAI) ──► ChromaDB (vectors + full metadata
                                            envelope per chunk)

  Chat:  query ──► embed ──► ChromaDB $and filter
                              (status==active AND conversation_id==X)
                              + ≤2-chunks-per-policy diversity cap
                           ──► system prompt + history + context ──► LLM
                           ──► answer + numbered citations
```

Everything is wired directly with the OpenAI SDK, FastAPI, raw SQL, and
ChromaDB's native filter API — no orchestration framework. At this scale
(three tables, four queries, one retrieval function) a framework would add
indirection without adding capability, and it's easier to reason about and
explain exactly what the pipeline does at each step.

## Stack

| Layer | Choice |
|---|---|
| Framework | FastAPI + Uvicorn (async throughout) |
| LLM | OpenAI `gpt-4o-mini` |
| Embeddings | OpenAI `text-embedding-3-small` |
| Vector store | ChromaDB (persistent, with `$and` metadata filter) |
| PDF parsing | PyMuPDF (`fitz`), local — no data leaves the machine during parsing |
| Metadata extraction | OpenAI JSON mode on first-page text |
| Chunking | Section-aware regex on numbered headings |
| Relational store | SQLite + `aiosqlite`, raw SQL (no ORM) |

Design conventions: async everywhere I/O happens, Pydantic v2 for every
request/response body, one OpenAI client shared across LLM/embedding/
extraction calls, and every endpoint returns a clean HTTP error instead of a
500 on malformed input, extraction failure, empty retrieval, or an LLM API
error.

## Data model

Every chunk indexed in ChromaDB carries this metadata envelope:

```
conversation_id, policy_id, version, effective_date (ISO YYYY-MM-DD),
status (active | superseded | interim | temporary), supersedes,
filename, page_number, chunk_index, document_type
```

Three fields do the load-bearing work: `status`, `effective_date`, and
`supersedes`.

SQLite holds three tables: `conversations`, `messages` (with citations as
JSON), and `documents` (filename, policy_id, version, status, upload
timestamp) — enough for an audit trail without an ORM.

Retrieval is **scoped per conversation**, not a global document store: every
upload is tagged with a `conversation_id`, and every query filters on
`conversation_id` **and** `status == "active"` together. That's what keeps
one chat session's documents isolated from another's.

## Running it

```bash
git clone https://github.com/ShakibaShahidiani/policy-assistant.git
cd policy-assistant
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add your OPENAI_API_KEY
python scripts/generate_sample_docs.py   # writes sample PDFs into pdfs/
uvicorn main:app --reload
```

Open `http://localhost:8000` for the chat UI, or drive it directly:

```bash
curl -F "file=@pdfs/reimbursement_2025.pdf" http://localhost:8000/upload
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"conversation_id": "<id from upload response>", "message": "What is the lodging limit?"}'
```

### Sample documents

The PDFs in `pdfs/` are **synthetic** — generated by
[`scripts/generate_sample_docs.py`](scripts/generate_sample_docs.py) for a
fictional company, specifically to exercise the version-resolution logic
(a superseded policy version, a self-described interim memo, a self-described
temporary pandemic-era exception, and a question that requires synthesizing
three different active policies). No real company policy content is used
anywhere in this repo. Regenerate or edit them freely.

## Demo

With the sample documents, three scenarios exercise the version-resolution
logic end-to-end:

**A — version conflict.** Upload `travel_expenses_2022.pdf` then
`reimbursement_2025.pdf` into one conversation.
- *"What is the lodging limit?"* → **$185 standard, $240 for designated
  high-cost cities with pre-approval** (the 2022 version's $160 flat limit
  is superseded and never retrieved).
- *"What's the submission deadline?"* → **21 days** (not the old 60-day
  window).

**B — stale documents.** Upload `home_office_guidelines.pdf`,
`workplace_policy.pdf`, and `information_security_data.pdf`.
- *"Can I use my personal laptop for work?"* → **No, prohibited except
  IT-approved devices** — the 2021 "temporary COVID measures" memo
  self-describes as temporary and is excluded; the 2025 Workplace Policy
  governs and the 2024 Information Security Policy's device-handling
  requirements are cited alongside it.

**C — multi-document synthesis.** Upload `Code of Business Conduct.pdf`,
`vendor_procurement_policy.pdf`, and `reimbursement_2025.pdf`.
- *"Can I accept a $150 dinner from a vendor?"* → cites all three: **must be
  disclosed** (Code of Conduct, >$120 threshold), **$50 meal cap** (Travel &
  Expense Policy), and **vendor-event compliance** (Vendor & Procurement
  Policy).

A fresh/empty conversation, or an off-topic question ("What is the capital of
France?"), correctly triggers the no-context refusal rather than a
hallucinated answer.

## Evaluation

[`evals/golden_set.py`](evals/golden_set.py) defines 9 cases against five
metrics — status resolution accuracy, retrieval recall, fact coverage,
LLM-judged faithfulness, and guardrail accuracy — each chosen because it can
*actually fail* (the hard `status == active` pre-filter already makes
version-leakage 100% by construction, so that's not reported as a quality
metric).

```bash
python evals/run_eval.py   # writes evals/results.json
```

The chat UI's **📊 Evaluation** button renders the latest `results.json` as a
scorecard (served via `GET /eval`).

## What's deliberately out of scope

This is a prototype for a case study, not a production system. Scoped out on
purpose: authentication/access control, department-level filtering,
re-ranking, a managed vector DB, parent-child chunking, and any
orchestration framework. The metadata-driven version resolution — the
actually hard part — is fully implemented.

## Project layout

```
main.py                  FastAPI app: /upload, /chat, /eval
ingest.py                PDF parsing, metadata extraction, chunking, retrieval
db.py                    SQLite schema + queries (conversations, messages, documents)
static/index.html        Single-page chat UI
SYSTEM_PROMPT.md          The /chat system prompt + design rationale
evals/golden_set.py      9-case eval set with expected outcomes
evals/run_eval.py        Eval harness (drives the real pipeline in-process)
scripts/generate_sample_docs.py   Generates the synthetic sample PDFs
check1-6_*.py            Standalone validation scripts used during development
```
