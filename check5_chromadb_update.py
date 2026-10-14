"""Check 5 — ChromaDB col.update() metadata preservation"""
import chromadb

client = chromadb.Client()
col = client.get_or_create_collection("test_update")

# Add a chunk with full metadata envelope (mirrors real chunk metadata)
original_metadata = {
    "conversation_id": "conv-A",
    "policy_id":       "FIN-TE-001",
    "version":         "4.0",
    "effective_date":  "2025-01-01",
    "status":          "active",
    "supersedes":      "FIN-TE-001-v2.1",
    "filename":        "reimbursement_2025.pdf",
    "page_number":     1,
    "chunk_index":     4,
    "document_type":   "policy",
}

col.add(
    ids=["chunk-1"],
    embeddings=[[0.1]*384],
    documents=["the standard lodging limit is USD 185"],
    metadatas=[original_metadata]
)

print("Before update:")
before = col.get(ids=["chunk-1"])["metadatas"][0]
for k, v in before.items():
    print(f"  {k}: {v!r}")

# Update ONLY the status field (as resolve_upload_status does)
col.update(
    ids=["chunk-1"],
    metadatas=[{"status": "superseded"}]
)

print("\nAfter update (status -> superseded):")
after = col.get(ids=["chunk-1"])["metadatas"][0]
for k, v in after.items():
    print(f"  {k}: {v!r}")

# Check which fields survived
expected_unchanged = {k: v for k, v in original_metadata.items() if k != "status"}
missing = []
wiped = []

for field, expected_val in expected_unchanged.items():
    got = after.get(field)
    if got is None and field not in after:
        missing.append(field)
    elif got != expected_val:
        wiped.append(f"  {field}: was {expected_val!r}, now {got!r}")

status_correct = after.get("status") == "superseded"

print()
if missing:
    print(f"FIELDS WIPED (missing entirely): {missing}")
if wiped:
    print(f"FIELDS CHANGED UNEXPECTEDLY:")
    for w in wiped:
        print(w)

if not missing and not wiped and status_correct:
    print("CHECK 5: PASS — col.update() only changed status, all other fields preserved")
else:
    print("CHECK 5: FAIL — update wiped metadata fields")
    print("  -> Fix: fetch full metadata first, patch status, then update")
