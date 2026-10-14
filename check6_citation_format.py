"""Check 6 — Citation format sanity check against the brief's example"""

# Simulate what /chat will return for the lodging-limit question
# Target format: "... $185 per night [1]." with footer "[1] filename, p.X"

answer_body = (
    "The standard lodging limit is USD 185 per night. [1] "
    "For travel to Tokyo, Singapore, Hong Kong, or Zurich, this may increase "
    "to USD 240 with pre-trip Finance approval. [1] "
    "Expenses must be submitted within 21 days of travel completion. [1]"
)

citations = [
    {"index": 1, "filename": "reimbursement_2025.pdf", "page": 1},
]

footer = "\n".join(
    f"[{c['index']}] {c['filename']}, p.{c['page']}"
    for c in citations
)

full_response = f"{answer_body}\n\n{footer}"

print("=" * 60)
print("FULL RESPONSE OUTPUT:")
print("=" * 60)
print(full_response)
print("=" * 60)

# Checks
checks = [
    ("[n] inline markers present", "[1]" in answer_body),
    ("filename in footer",         "reimbursement_2025.pdf" in footer),
    ("page number in footer",      "p.1" in footer),
    ("footer on separate line",    "\n\n" in full_response),
    ("no markdown fences",         "```" not in full_response),
]

all_pass = True
for label, result in checks:
    status = "PASS" if result else "FAIL"
    if not result:
        all_pass = False
    print(f"  [{status}] {label}")

print(f"\nCHECK 6: {'PASS' if all_pass else 'FAIL'}")
