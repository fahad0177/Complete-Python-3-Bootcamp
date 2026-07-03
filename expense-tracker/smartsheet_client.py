"""Minimal Smartsheet REST API client: add expense rows and attach the receipt file."""

import requests

from extractor import CATEGORIES

API_BASE = "https://api.smartsheet.com/2.0"

# Columns the expenses sheet is created with (and that add_expense_row fills in).
SHEET_COLUMNS = [
    {"title": "Vendor", "type": "TEXT_NUMBER", "primary": True},
    {"title": "Date", "type": "DATE"},
    {"title": "Category", "type": "PICKLIST", "options": CATEGORIES},
    {"title": "Description", "type": "TEXT_NUMBER"},
    {"title": "Amount", "type": "TEXT_NUMBER"},
    {"title": "GST", "type": "TEXT_NUMBER"},
    {"title": "Currency", "type": "TEXT_NUMBER"},
    {"title": "Notes", "type": "TEXT_NUMBER"},
]


def find_or_create_sheet(access_token: str, name: str = "Business Expenses") -> int:
    """Return the id of the sheet with this name, creating it if it doesn't exist yet."""
    headers = {"Authorization": f"Bearer {access_token}"}

    resp = requests.get(
        f"{API_BASE}/sheets", headers=headers, params={"includeAll": "true"}, timeout=30
    )
    resp.raise_for_status()
    for sheet in resp.json().get("data", []):
        if sheet["name"] == name:
            return sheet["id"]

    resp = requests.post(
        f"{API_BASE}/sheets",
        headers={**headers, "Content-Type": "application/json"},
        json={"name": name, "columns": SHEET_COLUMNS},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["result"]["id"]


class SmartsheetClient:
    def __init__(self, access_token: str, sheet_id: str):
        self.sheet_id = sheet_id
        self.headers = {"Authorization": f"Bearer {access_token}"}
        self._column_ids = None

    def _columns(self) -> dict:
        """Map column title -> column id, cached after first call."""
        if self._column_ids is None:
            resp = requests.get(
                f"{API_BASE}/sheets/{self.sheet_id}/columns",
                headers=self.headers,
                params={"pageSize": 100},
                timeout=30,
            )
            resp.raise_for_status()
            self._column_ids = {c["title"]: c["id"] for c in resp.json()["data"]}
        return self._column_ids

    def save_expense(self, expense: dict, filename: str, file_bytes: bytes, mime_type: str):
        """Add the row, attach the receipt to it, and keep the sheet date-sorted."""
        row_id = self.add_expense_row(expense)
        self.attach_receipt(row_id, filename, file_bytes, mime_type)
        self.sort_by_date()

    def add_expense_row(self, expense: dict) -> int:
        """Add one expense as a new row at the bottom of the sheet. Returns the row id."""
        cols = self._columns()
        values = {
            "Vendor": expense.get("vendor"),
            "Date": expense.get("date"),
            "Category": expense.get("category"),
            "Description": expense.get("description"),
            "Amount": expense.get("total_amount"),
            "GST": expense.get("gst_amount"),
            "Currency": expense.get("currency"),
            "Notes": expense.get("confidence_notes"),
        }
        cells = [
            {"columnId": cols[title], "value": value}
            for title, value in values.items()
            if title in cols and value not in (None, "")
        ]
        resp = requests.post(
            f"{API_BASE}/sheets/{self.sheet_id}/rows",
            headers={**self.headers, "Content-Type": "application/json"},
            json=[{"toBottom": True, "cells": cells}],
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["result"][0]["id"]

    def attach_receipt(self, row_id: int, filename: str, file_bytes: bytes, mime_type: str):
        """Attach the original receipt file to its row."""
        resp = requests.post(
            f"{API_BASE}/sheets/{self.sheet_id}/rows/{row_id}/attachments",
            headers={
                **self.headers,
                "Content-Type": mime_type,
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
            data=file_bytes,
            timeout=60,
        )
        resp.raise_for_status()

    def sort_by_date(self):
        """Re-sort the whole sheet by the Date column, oldest at the top."""
        cols = self._columns()
        if "Date" not in cols:
            return
        resp = requests.post(
            f"{API_BASE}/sheets/{self.sheet_id}/sort",
            headers={**self.headers, "Content-Type": "application/json"},
            json={"sortCriteria": [{"columnId": cols["Date"], "direction": "ASCENDING"}]},
            timeout=60,
        )
        resp.raise_for_status()

    def get_expenses(self, date_from: str = None, date_to: str = None) -> list:
        """Return rows (values + attachment info) with dates inside the range, oldest first.

        Dates are ISO strings (YYYY-MM-DD). Rows with no date are included only
        when no range is given.
        """
        resp = requests.get(
            f"{API_BASE}/sheets/{self.sheet_id}",
            headers=self.headers,
            params={"include": "attachments", "pageSize": 10000},
            timeout=60,
        )
        resp.raise_for_status()
        titles_by_id = {col_id: title for title, col_id in self._columns().items()}

        expenses = []
        for row in resp.json().get("rows", []):
            values = {
                titles_by_id[c["columnId"]]: c.get("value")
                for c in row.get("cells", [])
                if c.get("columnId") in titles_by_id
            }
            row_date = values.get("Date")
            if date_from and (not row_date or str(row_date) < date_from):
                continue
            if date_to and (not row_date or str(row_date) > date_to):
                continue
            expenses.append({"values": values, "attachments": row.get("attachments", [])})

        expenses.sort(key=lambda e: str(e["values"].get("Date") or ""))
        return expenses

    def download_attachment(self, attachment_id: int) -> tuple:
        """Fetch one attachment's original filename and file bytes."""
        resp = requests.get(
            f"{API_BASE}/sheets/{self.sheet_id}/attachments/{attachment_id}",
            headers=self.headers,
            timeout=30,
        )
        resp.raise_for_status()
        info = resp.json()
        file_resp = requests.get(info["url"], timeout=120)  # temporary signed URL
        file_resp.raise_for_status()
        return info.get("name", f"receipt-{attachment_id}"), file_resp.content

    def sheet_url(self) -> str:
        resp = requests.get(
            f"{API_BASE}/sheets/{self.sheet_id}",
            headers=self.headers,
            params={"pageSize": 1},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("permalink", "")
