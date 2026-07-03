"""Free storage backend: expenses in a Google Sheet, receipts in a Google Drive folder.

Auth is a Google service account (JSON key in GOOGLE_SERVICE_ACCOUNT_JSON).
On first use this creates:
  - a spreadsheet named "Business Expenses" (one row per expense, with a link
    to its receipt), and
  - a Drive folder named "Business Expense Receipts" holding the receipt files,
and shares both with SHARE_WITH_EMAIL so they show up in your own Google account.
"""

import io
import json
import os
import re

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

HEADER = ["Date", "Vendor", "Category", "Description", "Amount", "GST", "Currency", "Notes", "Receipt"]

TAB_NAME = "Expenses"


class GoogleExpenseBackend:
    def __init__(self):
        info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
        self.sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
        self.drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        self.share_with = os.environ.get("SHARE_WITH_EMAIL", "")
        self.sheet_name = os.environ.get("SHEET_NAME", "Business Expenses")
        self.folder_name = os.environ.get("FOLDER_NAME", "Business Expense Receipts")
        self._spreadsheet_id = None
        self._grid_id = None
        self._folder_id = None
        self._url = None

    # ---------- setup ----------

    def _find_file(self, name: str, mime_type: str):
        query = f"name = '{name}' and mimeType = '{mime_type}' and trashed = false"
        result = self.drive.files().list(q=query, fields="files(id, webViewLink)").execute()
        files = result.get("files", [])
        return files[0] if files else None

    def _share(self, file_id: str):
        """Give the owner's personal Google account access (as editor)."""
        if not self.share_with:
            return
        self.drive.permissions().create(
            fileId=file_id,
            body={"type": "user", "role": "writer", "emailAddress": self.share_with},
            sendNotificationEmail=False,
        ).execute()

    def _create_spreadsheet(self) -> str:
        ss = (
            self.sheets.spreadsheets()
            .create(
                body={
                    "properties": {"title": self.sheet_name},
                    "sheets": [{"properties": {"title": TAB_NAME}}],
                },
                fields="spreadsheetId,spreadsheetUrl,sheets(properties(sheetId))",
            )
            .execute()
        )
        spreadsheet_id = ss["spreadsheetId"]
        self._url = ss["spreadsheetUrl"]
        grid_id = ss["sheets"][0]["properties"]["sheetId"]

        # header row: write values, bold it, freeze it
        self.sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{TAB_NAME}!A1",
            valueInputOption="RAW",
            body={"values": [HEADER]},
        ).execute()
        self.sheets.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "requests": [
                    {
                        "repeatCell": {
                            "range": {"sheetId": grid_id, "startRowIndex": 0, "endRowIndex": 1},
                            "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                            "fields": "userEnteredFormat.textFormat.bold",
                        }
                    },
                    {
                        "updateSheetProperties": {
                            "properties": {"sheetId": grid_id, "gridProperties": {"frozenRowCount": 1}},
                            "fields": "gridProperties.frozenRowCount",
                        }
                    },
                ]
            },
        ).execute()

        self._share(spreadsheet_id)
        return spreadsheet_id

    def _ensure_setup(self):
        if self._spreadsheet_id and self._folder_id:
            return

        existing = self._find_file(self.sheet_name, "application/vnd.google-apps.spreadsheet")
        if existing:
            self._spreadsheet_id = existing["id"]
            self._url = existing.get("webViewLink", "")
        else:
            self._spreadsheet_id = self._create_spreadsheet()

        if self._grid_id is None:
            meta = (
                self.sheets.spreadsheets()
                .get(spreadsheetId=self._spreadsheet_id, fields="sheets(properties(sheetId,title)),spreadsheetUrl")
                .execute()
            )
            self._url = meta.get("spreadsheetUrl", self._url)
            props = meta["sheets"][0]["properties"]
            for s in meta["sheets"]:
                if s["properties"]["title"] == TAB_NAME:
                    props = s["properties"]
                    break
            self._grid_id = props["sheetId"]

        folder = self._find_file(self.folder_name, "application/vnd.google-apps.folder")
        if folder:
            self._folder_id = folder["id"]
        else:
            created = (
                self.drive.files()
                .create(
                    body={"name": self.folder_name, "mimeType": "application/vnd.google-apps.folder"},
                    fields="id",
                )
                .execute()
            )
            self._folder_id = created["id"]
            self._share(self._folder_id)

    # ---------- main operations ----------

    def save_expense(self, expense: dict, filename: str, file_bytes: bytes, mime_type: str):
        """Upload the receipt to Drive, append the expense row, keep the sheet date-sorted."""
        self._ensure_setup()

        receipt = (
            self.drive.files()
            .create(
                body={"name": filename, "parents": [self._folder_id]},
                media_body=MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=mime_type),
                fields="id, webViewLink",
            )
            .execute()
        )

        row = [
            expense.get("date") or "",
            expense.get("vendor") or "",
            expense.get("category") or "",
            expense.get("description") or "",
            expense.get("total_amount"),
            expense.get("gst_amount"),
            expense.get("currency") or "",
            expense.get("confidence_notes") or "",
            receipt.get("webViewLink", ""),
        ]
        self.sheets.spreadsheets().values().append(
            spreadsheetId=self._spreadsheet_id,
            range=f"{TAB_NAME}!A1",
            valueInputOption="RAW",  # keeps dates as ISO text so sorting/filtering is exact
            body={"values": [row]},
        ).execute()

        # sort everything below the header by the Date column, oldest first
        self.sheets.spreadsheets().batchUpdate(
            spreadsheetId=self._spreadsheet_id,
            body={
                "requests": [
                    {
                        "sortRange": {
                            "range": {"sheetId": self._grid_id, "startRowIndex": 1},
                            "sortSpecs": [{"dimensionIndex": 0, "sortOrder": "ASCENDING"}],
                        }
                    }
                ]
            },
        ).execute()

    def sheet_url(self) -> str:
        self._ensure_setup()
        return self._url or f"https://docs.google.com/spreadsheets/d/{self._spreadsheet_id}"

    # ---------- export ----------

    def get_expenses(self, date_from: str = None, date_to: str = None) -> list:
        """Rows in the date range, oldest first, with Drive file ids as attachments."""
        self._ensure_setup()
        result = (
            self.sheets.spreadsheets()
            .values()
            .get(spreadsheetId=self._spreadsheet_id, range=f"{TAB_NAME}!A2:I")
            .execute()
        )

        expenses = []
        for raw in result.get("values", []):
            raw = raw + [""] * (len(HEADER) - len(raw))
            values = dict(zip(HEADER, raw))
            row_date = values.get("Date")
            if date_from and (not row_date or str(row_date) < date_from):
                continue
            if date_to and (not row_date or str(row_date) > date_to):
                continue
            match = re.search(r"/d/([\w-]+)", values.get("Receipt", "") or "")
            attachments = [{"id": match.group(1)}] if match else []
            expenses.append({"values": values, "attachments": attachments})

        expenses.sort(key=lambda e: str(e["values"].get("Date") or ""))
        return expenses

    def download_attachment(self, file_id: str) -> tuple:
        meta = self.drive.files().get(fileId=file_id, fields="name").execute()
        content = self.drive.files().get_media(fileId=file_id).execute()
        return meta.get("name", f"receipt-{file_id}"), content
