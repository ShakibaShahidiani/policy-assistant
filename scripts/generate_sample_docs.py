"""
Generates the synthetic sample policy PDFs used by this repo's demo and eval suite.

These are entirely fictional, generic policy documents (no company name, real or
invented) written to exercise the version-resolution logic (superseded versions,
self-described interim/temporary guidance, multi-policy questions). No real
company policy content is used anywhere in this repo.

Run from the project root:

    python scripts/generate_sample_docs.py

Writes PDFs into pdfs/.
"""
import os
import fitz

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "pdfs")


def make_pdf(filename: str, title: str, meta_lines: list[str], body: str):
    doc = fitz.open()
    page = doc.new_page()
    y = 50

    page.insert_text((50, y), title, fontsize=16, fontname="helv")
    y += 28

    for line in meta_lines:
        page.insert_text((50, y), line, fontsize=10, fontname="helv")
        y += 16
    y += 10

    page.insert_textbox(
        fitz.Rect(50, y, 545, 780),
        body,
        fontsize=10.5,
        fontname="helv",
        lineheight=1.4,
    )

    doc.save(os.path.join(OUT_DIR, filename))
    doc.close()
    print(f"  wrote {filename}")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    make_pdf(
        "reimbursement_2025.pdf",
        "Travel & Expense Policy",
        ["Policy ID: FIN-TE-001", "Version: 4.0", "Effective Date: 1 January 2025"],
        "1. Purpose\n"
        "This policy governs reimbursement of business travel and expense "
        "claims for employees.\n\n"
        "2. Lodging\n"
        "2.1 The standard nightly lodging limit is USD 185.\n"
        "2.2 For designated high-cost cities (Tokyo, Singapore, Hong Kong, Zurich), "
        "the nightly limit increases to USD 240, subject to pre-trip Finance "
        "approval.\n\n"
        "3. Meals\n"
        "3.1 Meal reimbursement is capped at USD 50 per meal. Alcohol is "
        "reimbursable only when part of a documented client meal.\n\n"
        "4. Submission\n"
        "4.1 Expense claims must be submitted within 21 days of trip completion "
        "via the Expense Portal. Claims submitted after this window will be "
        "denied.",
    )

    make_pdf(
        "travel_expenses_2022.pdf",
        "Travel & Expense Policy",
        ["Policy ID: FIN-TE-001", "Version: 2.1", "Effective Date: 1 June 2022"],
        "1. Purpose\n"
        "This policy governs reimbursement of business travel for "
        "employees.\n\n"
        "2. Lodging\n"
        "2.1 The standard nightly lodging limit is USD 160.\n\n"
        "3. Submission\n"
        "3.1 Expense claims must be submitted within 60 days of trip completion.",
    )

    make_pdf(
        "data_guidelines.pdf",
        "Data Handling Interim Guidance",
        ["Policy ID: IT-MEMO-2019-07", "Effective Date: July 2019"],
        "1. Purpose\n"
        "This is interim guidance issued pending a comprehensive Information "
        "Security Policy and will be superseded once that policy is "
        "finalized.\n\n"
        "2. Storage\n"
        "2.1 USB drives and local hard drives may be used to store internal "
        "data with manager approval.",
    )

    make_pdf(
        "information_security_data.pdf",
        "Information Security Policy",
        ["Policy ID: IT-SEC-004", "Version: 5.0", "Effective Date: 1 November 2024"],
        "1. Purpose\n"
        "This policy sets data classification and handling requirements for "
        "employees.\n\n"
        "2. Data Classification\n"
        "2.1 Restricted data may not be stored on local drives or removable "
        "media.\n"
        "2.2 Personal cloud storage services are prohibited for company data "
        "of any classification.\n\n"
        "3. Device Requirements\n"
        "3.1 Only IT-approved devices may access restricted systems.",
    )

    make_pdf(
        "home_office_guidelines.pdf",
        "Home Office Guidelines (COVID-19 Temporary Measures)",
        ["Policy ID: HR-COVID-2021", "Effective Date: 2021"],
        "1. Purpose\n"
        "These are temporary measures introduced during the COVID-19 pandemic "
        "and will be withdrawn once normal office operations resume.\n\n"
        "2. Equipment\n"
        "2.1 Employees may use personal computers for work during the "
        "temporary home-office period.",
    )

    make_pdf(
        "workplace_policy.pdf",
        "Workplace & Flexible Work Policy",
        ["Policy ID: HR-WRK-012", "Version: 3.5", "Effective Date: 1 March 2025"],
        "1. Purpose\n"
        "This policy sets equipment and workplace standards for "
        "employees.\n\n"
        "2. Equipment\n"
        "2.1 Personal computers are prohibited for company work. Only "
        "IT-approved and IT-managed devices may be used, except where IT has "
        "granted explicit written approval.",
    )

    make_pdf(
        "vendor_procurement_policy.pdf",
        "Vendor & Procurement Policy",
        ["Policy ID: OPS-PROC-007", "Version: 2.0", "Effective Date: 1 February 2025"],
        "1. Purpose\n"
        "This policy governs vendor engagement for employees.\n\n"
        "2. Vendor Engagement\n"
        "2.1 Employees attending vendor-hosted events (meals, hospitality) "
        "must ensure the event complies with both the Travel & Expense Policy "
        "and the Code of Business Conduct.",
    )

    make_pdf(
        "Code of Business Conduct.pdf",
        "Code of Business Conduct",
        ["Policy ID: LEG-ETH-001", "Version: 4.0", "Effective Date: 1 January 2025"],
        "1. Purpose\n"
        "This Code sets ethical standards for employees.\n\n"
        "2. Gifts and Hospitality\n"
        "2.1 Gifts or hospitality from vendors or business partners valued "
        "over USD 120 must be disclosed to Compliance.\n"
        "2.2 Gifts or hospitality valued over USD 300 are prohibited outright, "
        "regardless of disclosure.\n\n"
        "3. Cross-reference\n"
        "3.1 Employees must also comply with the Travel & Expense Policy for "
        "any related reimbursement claims.",
    )

    print("\nDone.")


if __name__ == "__main__":
    main()
