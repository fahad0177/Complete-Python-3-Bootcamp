"""Free storage backend: expenses in a Google Sheet, receipts in a Google Drive folder.

Auth is a Google service account (JSON key in GOOGLE_SERVICE_ACCOUNT_JSON).

Recommended setup (avoids Drive permission/quota limits on personal Gmail):
  You create an empty Google Sheet and a Drive folder, share both with the
  service account's client_email as Editor, and provide their IDs as
  GOOGLE_SHEET_ID and GOOGLE_FOLDER_ID. The app fills them in — it never has to
  create files in the service account's own Drive.

Fallback (only works on Google Workspace / where the service account may own
files): if the IDs are not set, the app tries to create the sheet and folder
itself and share them with SHARE_WITH_EMAIL.
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


class GoogleExpenseBackend:
    def __init__(self):
        info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
        self.sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
        self.drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        self.share_with = os.environ.get("SHARE_WITH_EMAIL", "")
        self.sheet_name = os.environ.get("SHEET_NAME", "Business Expenses")
        self.folder_name = os.environ.get("FOLDER_NAME", "Business Expense Receipts")

        # Preferred: user-created, service-account-shared sheet + folder.
        self._spreadsheet_id = os.environ.get("GOOGLE_SHEET_ID") or None
        self._folder_id = os.environ.get("GOOGLE_FOLDER_ID") or None

        self._tab = None       # first tab's title, discovered at setup
        self._grid_id = None   # first tab's numeric id
        self._url = None
        self._ready = False

    # ---------- setup ----------

    def _share(self, file_id: str):
        if not self.share_with:
            return
        self.drive.permissions().create(
            fileId=file_id,
            body={"type": "user", "role": "writer", "emailAddress": self.share_with},
            sendNotificationEmail=False,
        ).execute()

    def _find_file(self, name: str, mime_type: str):
        query = f"name = '{name}' and mimeType = '{mime_type}' and trashed = false"
        result = self.drive.files().list(q=query, fields="files(id, webViewLink)").execute()
        files = result.get("files", [])
        return files[0] if files else None

    def _create_spreadsheet(self) -> str:
        ss = (
            self.sheets.spreadsheets()
            .create(
                body={"properties": {"title": self.sheet_name}},
                fields="spreadsheetId,spreadsheetUrl,sheets(properties(sheetId,title))",
            )
            .execute()
        )
        self._url = ss["spreadsheetUrl"]
        self._share(ss["spreadsheetId"])
        return ss["spreadsheetId"]

    def _create_folder(self) -> str:
        created = (
            self.drive.files()
            .create(
                body={"name": self.folder_name, "mimeType": "application/vnd.google-apps.folder"},
                fields="id",
            )
            .execute()
        )
        self._share(created["id"])
        return created["id"]

    def _ensure_header(self):
        """Make sure the first tab has our header row (bold + frozen)."""
        existing = (
            self.sheets.spreadsheets()
            .values()
            .get(spreadsheetId=self._spreadsheet_id, range=f"{self._tab}!A1:I1")
            .execute()
            .get("values", [])
        )
        if existing and existing[0]:
            return  # header already present
        self.sheets.spreadsheets().values().update(
            spreadsheetId=self._spreadsheet_id,
            range=f"{self._tab}!A1",
            valueInputOption="RAW",
            body={"values": [HEADER]},
        ).execute()
        self.sheets.spreadsheets().batchUpdate(
            spreadsheetId=self._spreadsheet_id,
            body={
                "requests": [
                    {
                        "repeatCell": {
                            "range": {"sheetId": self._grid_id, "startRowIndex": 0, "endRowIndex": 1},
                            "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                            "fields": "userEnteredFormat.textFormat.bold",
                        }
                    },
                    {
                        "updateSheetProperties": {
                            "properties": {"sheetId": self._grid_id, "gridProperties": {"frozenRowCount": 1}},
                            "fields": "gridProperties.frozenRowCount",
                        }
                    },
                ]
            },
        ).execute()

    def _ensure_setup(self):
        if self._ready:
            return

        # Spreadsheet: use the provided one, or (fallback) find/create by name.
        if not self._spreadsheet_id:
            existing = self._find_file(self.sheet_name, "application/vnd.google-apps.spreadsheet")
            self._spreadsheet_id = existing["id"] if existing else self._create_spreadsheet()

        meta = (
            self.sheets.spreadsheets()
            .get(spreadsheetId=self._spreadsheet_id, fields="spreadsheetUrl,sheets(properties(sheetId,title))")
            .execute()
        )
        self._url = meta.get("spreadsheetUrl", self._url)
        first_tab = meta["sheets"][0]["properties"]
        self._tab = first_tab["title"]
        self._grid_id = first_tab["sheetId"]
        self._ensure_header()

        # Folder: use the provided one, or (fallback) find/create by name.
        if not self._folder_id:
            folder = self._find_file(self.folder_name, "application/vnd.google-apps.folder")
            self._folder_id = folder["id"] if folder else self._create_folder()

        self._ready = True

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
                supportsAllDrives=True,
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
            range=f"{self._tab}!A1",
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
            .get(spreadsheetId=self._spreadsheet_id, range=f"{self._tab}!A2:I")
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
        meta = self.drive.files().get(fileId=file_id, fields="name", supportsAllDrives=True).execute()
        content = self.drive.files().get_media(fileId=file_id, supportsAllDrives=True).execute()
        return meta.get("name", f"receipt-{file_id}"), content
