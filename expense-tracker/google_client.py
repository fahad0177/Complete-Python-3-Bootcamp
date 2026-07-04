"""Free storage backend: expenses in a Google Sheet, receipts in a Google Drive folder.

Preferred auth: OAuth as the owner's own Google account (client id/secret +
refresh token). Files are then created in — and owned by — the user's Drive,
which avoids the "Service Accounts do not have storage quota" limitation.
Uses only the narrow drive.file scope: the app can touch just the files it
creates itself, nothing else in the user's Drive.

Legacy auth: a service account (GOOGLE_SERVICE_ACCOUNT_JSON), which only
works where the service account may own files (Google Workspace).
"""

import io
import json
import os
import re

from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# The only scope the OAuth flow asks for: access limited to files this app creates.
DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

HEADER = ["Date", "Vendor", "Category", "Description", "Amount", "GST", "Currency", "Notes", "Receipt"]


def oauth_backend(client_id: str, client_secret: str, refresh_token: str) -> "GoogleExpenseBackend":
    """Backend acting as the owner's own Google account (files land in their Drive)."""
    creds = OAuthCredentials(
        None,  # access token is minted automatically from the refresh token
        refresh_token=refresh_token,
        token_uri=GOOGLE_TOKEN_URL,
        client_id=client_id,
        client_secret=client_secret,
        scopes=[DRIVE_FILE_SCOPE],
    )
    return GoogleExpenseBackend(credentials=creds, use_env_ids=False)


class GoogleExpenseBackend:
    def __init__(self, credentials=None, use_env_ids=True):
        if credentials is None:  # legacy service-account path
            info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
            credentials = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
        self.sheets = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        self.drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
        self.share_with = os.environ.get("SHARE_WITH_EMAIL", "") if use_env_ids else ""
        self.sheet_name = os.environ.get("SHEET_NAME", "Business Expenses")
        self.folder_name = os.environ.get("FOLDER_NAME", "Business Expense Receipts")

        # Service-account mode can point at a user-created, shared sheet + folder.
        # OAuth mode always finds-or-creates its own (they're owned by the user anyway).
        self._spreadsheet_id = (os.environ.get("GOOGLE_SHEET_ID") or None) if use_env_ids else None
        self._folder_id = (os.environ.get("GOOGLE_FOLDER_ID") or None) if use_env_ids else None

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
