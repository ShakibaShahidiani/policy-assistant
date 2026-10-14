"""Check 4 — ChromaDB compound $and filter with conversation_id + status"""
import chromadb

# In-memory client — no persistence, throwaway
client = chromadb.Client()
col = client.get_or_create_collection("test_filter")

# Add 3 chunks with different status + conversation_id combos
col.add(
    ids=["chunk-1", "chunk-2", "chunk-3"],
    embeddings=[[0.1]*384, [0.2]*384, [0.3]*384],
    documents=["active doc in conv A", "superseded doc in conv A", "active doc in conv B"],
    metadatas=[
        {"status": "active",     "conversation_id": "conv-A", "policy_id": "POL-001"},
        {"status": "superseded", "conversation_id": "conv-A", "policy_id": "POL-001"},
        {"status": "active",     "conversation_id": "conv-B", "policy_id": "POL-002"},
    ]
)

print("Added 3 chunks:")
print("  chunk-1: status=active,     conv=conv-A")
print("  chunk-2: status=superseded, conv=conv-A")
print("  chunk-3: status=active,     conv=conv-B")
print()

# Query: must return only chunk-1
res = col.query(
    query_embeddings=[[0.1]*384],
    n_results=3,
    where={"$and": [
        {"status":          {"$eq": "active"}},
        {"conversation_id": {"$eq": "conv-A"}}
    ]}
)

ids_returned = res["ids"][0]
docs_returned = res["documents"][0]

print(f"Query: status==active AND conversation_id==conv-A")
print(f"IDs returned: {ids_returned}")
print(f"Docs returned: {docs_returned}")
print()

if ids_returned == ["chunk-1"]:
    print("CHECK 4: PASS — $and filter works correctly, returns exactly chunk-1")
else:
    print(f"CHECK 4: FAIL — expected ['chunk-1'], got {ids_returned}")
    print("  -> ChromaDB $and syntax may have version-specific quirks, investigate")
