import json
import os
import tempfile
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from db import (get_or_create_conversation, get_history, init_db,
                save_document, save_message)
from ingest import (build_chunk_meta, chunk_pages, client as openai_client,
                    col, embed_chunks, embed_query, extract_metadata,
                    parse_pdf, resolve_upload_status, retrieve)

SYSTEM_PROMPT = """
You are an internal policy assistant. Your role is to answer employee
questions accurately, using only the policy excerpts retrieved for each
query.

## Context Format

Policy excerpts are provided as numbered blocks:

  [n] filename.pdf, p.X:
  <excerpt text>

Each [n] is a citation marker. Use it to attribute claims in your answer.

## Rule 1 — Answer Only From Retrieved Context

Do not use general knowledge or any information outside the provided excerpts.
Every factual claim in your answer must be traceable to a specific [n] block.

If no relevant excerpt was retrieved, respond with:
  "I cannot find relevant information on this topic in the uploaded documents."

Do not invent, infer, or extrapolate beyond what the excerpts state.

## Rule 2 — Always Surface Material Exceptions

Never state a limit, threshold, or requirement in isolation if the retrieved
context contains conditions or exceptions that would change what an employee
should actually do.

Examples:
- If context gives a standard lodging limit AND a higher limit for specific
  cities with approval conditions, state both.
- If a reimbursement rule has a submission deadline AND a hard denial cutoff,
  state both.

If in doubt: include the exception. Omitting it is the more costly error.

## Rule 3 — Resolve Conflicts by Recency

When multiple active policies address the same topic:

1. Treat the policy with the most recent effective date as governing.
2. Still cite any other active policy that adds explicit, relevant requirements
   — even if it does not contradict the newer policy.
3. Do not silently drop an active policy from your answer because a newer one
   exists. Only exclude it if its content is entirely redundant.

## Rule 4 — Synthesise Across Policies When Needed

If a question involves multiple policy domains (e.g. expense limits, ethics,
and procurement), address each dimension with its own citation. Do not answer
from only one policy when retrieved context shows others are relevant.

Structure multi-policy answers by domain:
- Lead with the most directly responsive policy.
- Add each additional policy as a separate point with its own [n].
- Close with a summary of what the employee must do.

## Rule 5 — Flag Cross-Referenced Missing Policies

If a retrieved excerpt explicitly names another policy that was not retrieved
in this session, flag it by name at the end of your answer:

  "Note: this excerpt references [Policy Name], which was not found in the
  uploaded documents. That policy may also apply."

Do not flag policies that are merely implied or that you believe should exist.
Only flag what the retrieved text itself names.

## Citation Format

Inline: place [n] immediately after the specific claim it supports.
Footer: list every cited source at the end of your response.

Footer format (one line per source):
  [n] filename.pdf, p.X

Multiple claims from the same source share one citation number.
If a claim draws on two sources, place both markers: [1][2].

## Style

Professional and direct. Authoritative without being stiff. Match the
register of a knowledgeable compliance colleague, not a legal document.

- Keep answers concise for simple questions.
- Use brief structure (e.g. numbered points) only for multi-policy answers
  where it genuinely aids clarity.
- Do not hedge or soften beyond what the context itself warrants.
- Do not suggest the employee seek further clarification unless the retrieved
  context is genuinely ambiguous or incomplete.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Policy Assistant API", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def index():
    with open("static/index.html") as f:
        return f.read()


@app.get("/eval")
async def get_eval():
    """Serve the latest golden-set evaluation results (run: python evals/run_eval.py)."""
    path = os.path.join("evals", "results.json")
    if not os.path.exists(path):
        raise HTTPException(
            status_code=404,
            detail="No evaluation results yet. Run: python evals/run_eval.py",
        )
    with open(path) as f:
        return json.load(f)


class UploadResponse(BaseModel):
    conversation_id: str
    filename: str
    chunks_indexed: int
    status: str
    supersedes: str | None
    superseded_filename: str | None = None


class ChatRequest(BaseModel):
    conversation_id: str
    message: str


class CitationItem(BaseModel):
    index: int
    filename: str
    page: int


class ChatResponse(BaseModel):
    conversation_id: str
    answer: str
    citations: list[CitationItem]


@app.post("/upload", response_model=UploadResponse)
async def upload(
    file: UploadFile,
    conversation_id: str = Form(None),
):
    # Validate PDF
    is_pdf = (
        file.content_type == "application/pdf"
        or (file.filename or "").lower().endswith(".pdf")
    )
    if not is_pdf:
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    # Session
    if not conversation_id:
        conversation_id = str(uuid.uuid4())
    await get_or_create_conversation(conversation_id)

    # Write to temp file for PyMuPDF
    contents = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    try:
        try:
            pages = parse_pdf(tmp_path)
        except Exception:
            raise HTTPException(status_code=400, detail="Failed to parse PDF: file is not a valid PDF or is corrupted")
        if not pages:
            raise HTTPException(status_code=400, detail="PDF contains no extractable text")
    finally:
        os.unlink(tmp_path)

    # Metadata extraction
    try:
        meta = await extract_metadata(pages[0]["text"])
    except (json.JSONDecodeError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=f"Metadata extraction returned malformed JSON: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"LLM API error during metadata extraction: {exc}")

    meta["filename"] = file.filename

    # Resolve version status (supersession check)
    meta = await resolve_upload_status(conversation_id, meta, col)

    print(
        f"\n[INGEST] {file.filename}"
        f"\n  policy_id={meta.get('policy_id')}  version={meta.get('version')}"
        f"  effective_date={meta.get('effective_date')}  status={meta.get('status')}"
        f"  supersedes={meta.get('supersedes')}"
    )

    # Chunk → embed → index
    chunks = chunk_pages(pages, meta)

    try:
        embeddings = await embed_chunks([c["text"] for c in chunks])
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Embedding API error: {exc}")

    ids = [str(uuid.uuid4()) for _ in chunks]
    documents = [c["text"] for c in chunks]
    metadatas = [build_chunk_meta(c, conversation_id, file.filename) for c in chunks]
    col.add(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)

    await save_document(file.filename, conversation_id, meta)

    return UploadResponse(
        conversation_id=conversation_id,
        filename=file.filename,
        chunks_indexed=len(chunks),
        status=meta["status"],
        supersedes=meta.get("supersedes"),
        superseded_filename=meta.get("superseded_filename"),
    )


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message must not be empty")

    await get_or_create_conversation(req.conversation_id)

    # Conversation history (last 5)
    history = await get_history(req.conversation_id)

    # Embed query + retrieve
    try:
        query_vec = await embed_query(req.message)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Embedding API error: {exc}")

    chunks = retrieve(query_vec, req.conversation_id)

    # Empty retrieval — do not call LLM
    if not chunks:
        no_context = "I cannot find relevant information on this topic in the uploaded documents."
        await save_message(req.conversation_id, "user", req.message)
        await save_message(req.conversation_id, "assistant", no_context)
        return ChatResponse(
            conversation_id=req.conversation_id,
            answer=no_context,
            citations=[],
        )

    # Build context blocks with citation numbers
    context_blocks = []
    citation_map: dict[int, CitationItem] = {}
    for i, (doc, md) in enumerate(chunks, start=1):
        filename = md.get("filename", "unknown")
        page = md.get("page_number", 0)
        context_blocks.append(f"[{i}] {filename}, p.{page}:\n{doc}")
        citation_map[i] = CitationItem(index=i, filename=filename, page=page)

    context_str = "\n\n".join(context_blocks)

    # Build history messages
    history_messages = [{"role": m["role"], "content": m["content"]} for m in history]

    messages = (
        [{"role": "system", "content": SYSTEM_PROMPT}]
        + history_messages
        + [{"role": "user", "content": f"{context_str}\n\nQuestion: {req.message}"}]
    )

    # LLM call
    try:
        completion = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"LLM API error: {exc}. Please retry.")

    answer = completion.choices[0].message.content

    # Persist
    citations_json = json.dumps([c.model_dump() for c in citation_map.values()])
    await save_message(req.conversation_id, "user", req.message)
    await save_message(req.conversation_id, "assistant", answer, citations_json)

    return ChatResponse(
        conversation_id=req.conversation_id,
        answer=answer,
        citations=list(citation_map.values()),
    )
