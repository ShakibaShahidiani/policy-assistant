"""Check 2 — PyMuPDF first-page scan on all 8 PDFs"""
import fitz
import os

PDFS = [
    "pdfs/Code of Business Conduct.pdf",
    "pdfs/data_guidelines.pdf",
    "pdfs/home_office_guidelines.pdf",
    "pdfs/information_security_data.pdf",
    "pdfs/reimbursement_2025.pdf",
    "pdfs/travel_expenses_2022.pdf",
    "pdfs/vendor_procurement_policy.pdf",
    "pdfs/workplace_policy.pdf",
]

all_pass = True

for path in PDFS:
    filename = os.path.basename(path)
    print(f"\n{'='*60}")
    print(f"FILE: {filename}")
    print('='*60)
    try:
        doc = fitz.open(path)
        first_page_text = doc[0].get_text()
        doc.close()
        if first_page_text.strip():
            print(first_page_text.strip())
            print(f"\n  -> OK: first page extracted ({len(first_page_text)} chars)")
        else:
            print("  -> WARNING: first page is empty or whitespace only")
            all_pass = False
    except Exception as e:
        print(f"  -> ERROR: {e}")
        all_pass = False

print(f"\n{'='*60}")
print(f"CHECK 2: {'PASS' if all_pass else 'FAIL'}")
