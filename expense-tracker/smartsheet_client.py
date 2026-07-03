"""Minimal Smartsheet REST API client: add expense rows and attach the receipt file."""

import requests

API_BASE = "https://api.smartsheet.com/2.0"

# Column titles the sheet must have (setup_sheet.py creates them).
COLUMNS = ["Vendor", "Date", "Category", "Description", "Amount", "GST", "Currency", "Notes"]


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

    def sheet_url(self) -> str:
        resp = requests.get(
            f"{API_BASE}/sheets/{self.sheet_id}",
            headers=self.headers,
            params={"pageSize": 1},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("permalink", "")
