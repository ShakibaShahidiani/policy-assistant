"""Check 3 — OpenAI metadata extraction on 3 docs, compare against the oracle"""
import asyncio
import json
import fitz
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()
client = AsyncOpenAI()

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

async def extract_metadata(first_page_text: str) -> dict:
    resp = await client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": first_page_text}
        ]
    )
    return json.loads(resp.choices[0].message.content)

def get_first_page(path: str) -> str:
    doc = fitz.open(path)
    text = doc[0].get_text()
    doc.close()
    return text

# Oracle expectations for these 3 docs
ORACLE = {
    "reimbursement_2025.pdf": {
        "policy_id": "FIN-TE-001",
        "version": "4.0",
        "effective_date": "2025-01-01",
        "self_described_status": "active",
    },
    "travel_expenses_2022.pdf": {
        "policy_id": "FIN-TE-001",
        "version": "2.1",
        "effective_date": "2022-06-01",
        "self_described_status": "active",
    },
    "home_office_guidelines.pdf": {
        "policy_id": "HR-COVID-2021",
        "version": None,
        "effective_date": "2021-01-01",
        "self_described_status": "temporary",
    },
}

async def main():
    docs = [
        ("reimbursement_2025.pdf",   "pdfs/reimbursement_2025.pdf"),
        ("travel_expenses_2022.pdf", "pdfs/travel_expenses_2022.pdf"),
        ("home_office_guidelines.pdf", "pdfs/home_office_guidelines.pdf"),
    ]

    all_pass = True

    for filename, path in docs:
        print(f"\n{'='*60}")
        print(f"FILE: {filename}")
        print('='*60)

        first_page = get_first_page(path)
        result = await extract_metadata(first_page)
        oracle = ORACLE[filename]

        print(f"Extracted: {json.dumps(result, indent=2)}")
        print(f"Oracle:    {json.dumps(oracle, indent=2)}")

        mismatches = []
        for field, expected in oracle.items():
            got = result.get(field)
            if got != expected:
                mismatches.append(f"  {field}: expected={expected!r}, got={got!r}")

        if mismatches:
            print(f"\n  MISMATCHES:")
            for m in mismatches:
                print(m)
            print(f"\n  -> FAIL")
            all_pass = False
        else:
            print(f"\n  -> PASS")

    print(f"\n{'='*60}")
    print(f"CHECK 3: {'PASS' if all_pass else 'FAIL — fix extraction prompt before building resolve_upload_status'}")

asyncio.run(main())
