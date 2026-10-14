"""Check 1 — PyMuPDF table fidelity on reimbursement_2025.pdf"""
import fitz

def parse_pdf(path: str) -> list[dict]:
    doc = fitz.open(path)
    pages = []
    for page_num, page in enumerate(doc, start=1):
        text = page.get_text()
        if text.strip():
            pages.append({"page_number": page_num, "text": text})
    doc.close()
    return pages

pages = parse_pdf("pdfs/reimbursement_2025.pdf")
full_text = "\n".join(p["text"] for p in pages)

print(f"Pages extracted: {len(pages)}\n")
print("=" * 60)
print(full_text)
print("=" * 60)

found_185 = "185" in full_text
found_240 = "240" in full_text
found_50 = "50" in full_text

print(f"\n'185' found: {found_185}")
print(f"'240' found: {found_240}")
print(f"'50' found: {found_50}")

if found_185 and found_240 and found_50:
    print("\nCHECK 1: PASS")
else:
    print("\nCHECK 1: FAIL — missing values, need to switch to page.get_text('blocks')")
