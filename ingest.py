import re
import json
import fitz
import chromadb
from collections import defaultdict
from openai import AsyncOpenAI
from dotenv import load_dotenv

from db import get_active_doc, mark_doc_superseded

load_dotenv()

client = AsyncOpenAI()
_chroma = chromadb.PersistentClient(path="./chroma_store")
col = _chroma.get_or_create_collection("policies")

EXTRACTION_SYSTEM_PROMPT = """
Extract policy metadata from the document text and return ONLY a JSON object with these fields:
{
  "policy_id": "The policy identifier. May be labelled 'Policy ID', 'Reference', or similar. E.g. 'FIN-TE-001'.",
  "version": "Version number if present (e.g. '3.2'). Use null if not stated.",
  "effective_date": "Effective or issue date as ISO YYYY-MM-DD. Formats vary: '1 January 2025' -> '2025-01-01', 'July 2019' -> '2019-07-01', '2021' -> '2021-01-01'.",
  "document_type": "One of: policy, memo, guideline",
  "self_described_status": "Does the document EXPLICITLY describe itself as interim, temporary, or historical/superseded? E.g. 'temporary measures during COVID-19', 'interim guidance pending a comprehensive policy'. Return 'active' unless it explicitly does. One of: active, interim, temporary, historical"
}
Return only valid JSON. No explanation, no markdown fences.
"""


def parse_pdf(path: str) -> list[dict]:
    doc = fitz.open(path)
    pages = []
    for page_num, page in enumerate(doc, start=1):
        text = page.get_text()
        if text.strip():
            pages.append({"page_number": page_num, "text": text})
    doc.close()
    return pages


async def extract_metadata(first_page_text: str) -> dict:
    resp = await client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": first_page_text},
        ],
    )
    return json.loads(resp.choices[0].message.content)


def chunk_pages(pages: list[dict], meta: dict) -> list[dict]:
    chunks = []
    global_idx = 0
    for page in pages:
        parts = re.split(r'(?=\n\d+(?:\.\d+)*\.\s+\S)', "\n" + page["text"])
        for part in parts:
            part = part.strip()
            if not part:
                continue
            chunks.append({
                "text":           part,
                "page_number":    page["page_number"],
                "chunk_index":    global_idx,
                "policy_id":      meta.get("policy_id"),
                "version":        meta.get("version"),
                "effective_date": meta.get("effective_date"),
                "status":         meta.get("status"),
                "supersedes":     meta.get("supersedes"),
                "document_type":  meta.get("document_type"),
            })
            global_idx += 1
    return chunks


def build_chunk_meta(chunk: dict, conversation_id: str, filename: str) -> dict:
    """Build ChromaDB metadata dict; None → '' (ChromaDB requires scalar types)."""
    return {
        "conversation_id": conversation_id,
        "policy_id":       chunk.get("policy_id") or "",
        "version":         chunk.get("version") or "",
        "effective_date":  chunk.get("effective_date") or "",
        "status":          chunk.get("status") or "active",
        "supersedes":      chunk.get("supersedes") or "",
        "filename":        filename,
        "page_number":     chunk.get("page_number", 0),
        "chunk_index":     chunk.get("chunk_index", 0),
        "document_type":   chunk.get("document_type") or "",
    }


async def embed_chunks(texts: list[str]) -> list[list[float]]:
    resp = await client.embeddings.create(
        model="text-embedding-3-small",
        input=texts,
    )
    return [item.embedding for item in resp.data]


async def embed_query(query: str) -> list[float]:
    resp = await client.embeddings.create(
        model="text-embedding-3-small",
        input=[query],
    )
    return resp.data[0].embedding


async def resolve_upload_status(conversation_id: str,
                                new_meta: dict,
                                collection) -> dict:
    new_meta["status"] = "active"
    new_meta["supersedes"] = None

    # Self-describing stale docs — caught directly, no cross-doc comparison needed
    self_status = new_meta.get("self_described_status", "active")
    if self_status in ("interim", "temporary", "historical"):
        new_meta["status"] = self_status
        return new_meta

    policy_id = new_meta.get("policy_id")
    if not policy_id:
        return new_meta

    existing = await get_active_doc(conversation_id, policy_id)
    if not existing:
        return new_meta  # first doc with this policy_id in this conversation

    new_date = new_meta.get("effective_date") or ""
    existing_date = existing.get("effective_date") or ""

    if new_date > existing_date:
        # New doc is newer — supersede the existing one in SQLite + ChromaDB
        await mark_doc_superseded(conversation_id, policy_id)
        old = collection.get(where={"$and": [
            {"conversation_id": {"$eq": conversation_id}},
            {"policy_id":       {"$eq": policy_id}},
            {"status":          {"$eq": "active"}},
        ]})
        if old["ids"]:
            # Partial update preserves all other metadata fields
            collection.update(
                ids=old["ids"],
                metadatas=[{"status": "superseded"} for _ in old["ids"]],
            )
        existing_version = existing.get("version") or "unknown"
        new_meta["supersedes"] = f"{policy_id}-v{existing_version}"
        new_meta["superseded_filename"] = existing.get("filename")
    else:
        # New doc is older than one already in this conversation
        new_meta["status"] = "superseded"

    return new_meta


def retrieve(query_vec: list, conversation_id: str,
             k_wide: int = 15, per_policy: int = 2, keep: int = 7) -> list[tuple]:
    n_results = min(k_wide, col.count())
    if n_results == 0:
        return []
    res = col.query(
        query_embeddings=[query_vec],
        n_results=n_results,
        where={"$and": [
            {"status":          {"$eq": "active"}},
            {"conversation_id": {"$eq": conversation_id}},
        ]},
    )
    hits = list(zip(res["documents"][0], res["metadatas"][0]))
    seen: dict[str, int] = defaultdict(int)
    out = []
    for doc, md in hits:
        pid = md.get("policy_id", "")
        if seen[pid] < per_policy:
            out.append((doc, md))
            seen[pid] += 1
    return out[:keep]
