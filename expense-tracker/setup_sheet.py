"""One-time setup: creates the "Business Expenses" sheet in Smartsheet and prints its ID.

Usage:
    python setup_sheet.py

Requires SMARTSHEET_ACCESS_TOKEN in your environment or .env file.
Copy the printed sheet ID into .env as SMARTSHEET_SHEET_ID.
"""

import os
import sys

import requests
from dotenv import load_dotenv

from extractor import CATEGORIES
from smartsheet_client import API_BASE


def main():
    load_dotenv()
    token = os.environ.get("SMARTSHEET_ACCESS_TOKEN")
    if not token:
        sys.exit("Set SMARTSHEET_ACCESS_TOKEN in .env first (see .env.example)")

    sheet_spec = {
        "name": "Business Expenses",
        "columns": [
            {"title": "Vendor", "type": "TEXT_NUMBER", "primary": True},
            {"title": "Date", "type": "DATE"},
            {"title": "Category", "type": "PICKLIST", "options": CATEGORIES},
            {"title": "Description", "type": "TEXT_NUMBER"},
            {"title": "Amount", "type": "TEXT_NUMBER"},
            {"title": "GST", "type": "TEXT_NUMBER"},
            {"title": "Currency", "type": "TEXT_NUMBER"},
            {"title": "Notes", "type": "TEXT_NUMBER"},
        ],
    }

    resp = requests.post(
        f"{API_BASE}/sheets",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=sheet_spec,
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()["result"]

    print("Sheet created!")
    print(f"  Name:      {result['name']}")
    print(f"  Sheet ID:  {result['id']}")
    print(f"  Link:      {result['permalink']}")
    print()
    print(f"Add this line to your .env file:")
    print(f"  SMARTSHEET_SHEET_ID={result['id']}")


if __name__ == "__main__":
    main()
