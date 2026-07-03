"""Optional one-time setup: creates the "Business Expenses" sheet and prints its ID.

You normally don't need this — the app creates the sheet automatically on the
first upload. Run it only if you want the sheet to exist ahead of time:

    python setup_sheet.py
"""

import os
import sys

from dotenv import load_dotenv

from smartsheet_client import find_or_create_sheet


def main():
    load_dotenv()
    token = os.environ.get("SMARTSHEET_ACCESS_TOKEN")
    if not token:
        sys.exit("Set SMARTSHEET_ACCESS_TOKEN in .env first (see .env.example)")

    name = os.environ.get("SHEET_NAME", "Business Expenses")
    sheet_id = find_or_create_sheet(token, name)
    print(f'Sheet "{name}" is ready. Sheet ID: {sheet_id}')


if __name__ == "__main__":
    main()
