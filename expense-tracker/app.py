"""Expense tracker web app.

Upload a receipt (photo or PDF) -> Claude extracts the details ->
a row is added to your expense sheet with the receipt file stored alongside.

Storage backends (picked automatically from configuration):
  - Google Sheets + Drive (free) when GOOGLE_SERVICE_ACCOUNT_JSON is set
  - Smartsheet when SMARTSHEET_ACCESS_TOKEN is set

Works locally (python app.py) or deployed to a host like Render (gunicorn app:app).
If APP_PASSWORD is set, the app asks for it once per device and remembers you.
The sheet (and Drive folder) are created automatically on the first upload.
"""

import csv
import hashlib
import io
import os
import re
import secrets
import zipfile
from datetime import date
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv
from flask import Flask, redirect, render_template, request, send_file, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from extractor import extract_receipt, IMAGE_MEDIA_TYPES

load_dotenv()

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")

app = Flask(__name__)
# Behind Render's proxy: trust X-Forwarded-* so generated URLs use https.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB upload cap
# Stable secret so login cookies survive server restarts.
app.secret_key = hashlib.sha256(f"expense-tracker:{APP_PASSWORD}".encode()).digest()

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"

ALLOWED_EXTENSIONS = set(IMAGE_MEDIA_TYPES) | {".pdf"}

_backend = None  # cached after first use
_runtime_google_token = None  # refresh token from an in-app connect, until saved as env var


def _google_oauth_configured() -> bool:
    return bool(
        os.environ.get("GOOGLE_OAUTH_CLIENT_ID") and os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    )


def _google_refresh_token():
    return os.environ.get("GOOGLE_REFRESH_TOKEN") or _runtime_google_token


def get_backend():
    """Pick the storage backend from what's configured.

    1. Google Sheets + Drive as the owner's own account (free) when
       GOOGLE_OAUTH_CLIENT_ID/SECRET are set — recommended.
    2. Google via a service account (Workspace only) when GOOGLE_SERVICE_ACCOUNT_JSON is set.
    3. Smartsheet when SMARTSHEET_ACCESS_TOKEN is set.
    """
    global _backend
    if _backend is None:
        if _google_oauth_configured():
            token = _google_refresh_token()
            if not token:
                raise RuntimeError(
                    "Google Drive isn't connected yet — go to the home page and tap "
                    "'Connect Google Drive' (one time)."
                )
            from google_client import oauth_backend

            _backend = oauth_backend(
                os.environ["GOOGLE_OAUTH_CLIENT_ID"],
                os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
                token,
            )
        elif os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"):
            from google_client import GoogleExpenseBackend

            _backend = GoogleExpenseBackend()
        elif os.environ.get("SMARTSHEET_ACCESS_TOKEN"):
            from smartsheet_client import SmartsheetClient, find_or_create_sheet

            token = os.environ["SMARTSHEET_ACCESS_TOKEN"]
            sheet_id = os.environ.get("SMARTSHEET_SHEET_ID") or find_or_create_sheet(
                token, os.environ.get("SHEET_NAME", "Business Expenses")
            )
            _backend = SmartsheetClient(token, sheet_id)
        else:
            raise KeyError("GOOGLE_OAUTH_CLIENT_ID (or SMARTSHEET_ACCESS_TOKEN)")
    return _backend


def mime_type_for(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    return "application/pdf" if ext == ".pdf" else IMAGE_MEDIA_TYPES[ext]


@app.before_request
def require_login():
    if not APP_PASSWORD:
        return  # no password configured -> open (local use)
    if request.endpoint in ("login", "static"):
        return
    if not session.get("authed"):
        return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("password", "") == APP_PASSWORD:
            session["authed"] = True
            session.permanent = True  # stay logged in on this device
            return redirect(url_for("index"))
        error = "Wrong password, try again."
    return render_template("login.html", error=error)


@app.route("/")
def index():
    return render_template(
        "index.html",
        needs_google_connect=_google_oauth_configured() and not _google_refresh_token(),
    )


@app.route("/google/auth")
def google_auth():
    """Send the owner to Google's consent screen (one-time connect)."""
    if not _google_oauth_configured():
        return render_template(
            "index.html",
            error="Google OAuth isn't configured — set GOOGLE_OAUTH_CLIENT_ID and "
            "GOOGLE_OAUTH_CLIENT_SECRET in the app's environment first.",
        )
    state = secrets.token_urlsafe(16)
    session["oauth_state"] = state
    params = urlencode(
        {
            "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"],
            "redirect_uri": url_for("google_callback", _external=True),
            "response_type": "code",
            "scope": DRIVE_FILE_SCOPE,  # only files this app creates — nothing else
            "access_type": "offline",   # so we get a long-lived refresh token
            "prompt": "consent",
            "state": state,
        }
    )
    return redirect(f"{GOOGLE_AUTH_URL}?{params}")


@app.route("/google/callback")
def google_callback():
    """Google redirects back here; swap the code for a long-lived refresh token."""
    global _backend, _runtime_google_token

    if request.args.get("state") != session.pop("oauth_state", None):
        return render_template(
            "index.html", needs_google_connect=True,
            error="The Google sign-in session expired — please tap Connect again.",
        )
    if request.args.get("error"):
        return render_template(
            "index.html", needs_google_connect=True,
            error=f"Google connection was cancelled: {request.args['error']}",
        )

    resp = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "code": request.args.get("code", ""),
            "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
            "redirect_uri": url_for("google_callback", _external=True),
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    tokens = resp.json()
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        detail = tokens.get("error_description") or tokens.get("error") or resp.text[:200]
        return render_template(
            "index.html", needs_google_connect=True,
            error=f"Google didn't return a connection token ({detail}) — please try again.",
        )

    _runtime_google_token = refresh_token  # works immediately, until the app restarts
    _backend = None
    return render_template(
        "google_connected.html",
        refresh_token=refresh_token,
        already_saved=bool(os.environ.get("GOOGLE_REFRESH_TOKEN")),
    )


@app.route("/upload", methods=["POST"])
def upload():
    file = request.files.get("receipt")
    if not file or not file.filename:
        return render_template("index.html", error="Please choose a receipt file first.")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return render_template(
            "index.html",
            error=f"Unsupported file type '{ext}'. Upload a PDF, JPG, PNG, GIF or WebP.",
        )

    file_bytes = file.read()

    try:
        expense = extract_receipt(file_bytes, file.filename)
    except Exception as exc:
        return render_template("index.html", error=f"Could not read the receipt: {exc}")

    try:
        backend = get_backend()
        backend.save_expense(expense, file.filename, file_bytes, mime_type_for(file.filename))
        sheet_url = backend.sheet_url()
    except KeyError as exc:
        return render_template(
            "index.html",
            error=f"Missing environment variable {exc}. Check the app's settings.",
            expense=expense,
        )
    except Exception as exc:
        return render_template(
            "index.html",
            error=f"Extracted the receipt but could not save it to your expense sheet: {exc}",
            expense=expense,
        )

    return render_template("index.html", expense=expense, sheet_url=sheet_url)


def _financial_year_start(today: date) -> str:
    """First day of the current Australian financial year (1 July)."""
    year = today.year if today.month >= 7 else today.year - 1
    return f"{year}-07-01"


def _safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-") or "unknown"


@app.route("/export")
def export_page():
    today = date.today()
    return render_template(
        "export.html",
        default_from=_financial_year_start(today),
        default_to=today.isoformat(),
    )


@app.route("/export/download")
def export_download():
    date_from = request.args.get("from") or None
    date_to = request.args.get("to") or None

    try:
        backend = get_backend()
        expenses = backend.get_expenses(date_from, date_to)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            # CSV summary of everything in the range
            csv_buf = io.StringIO()
            writer = csv.writer(csv_buf)
            writer.writerow(
                ["Date", "Vendor", "Category", "Description", "Amount", "GST", "Currency", "Notes"]
            )
            for e in expenses:
                v = e["values"]
                writer.writerow(
                    [v.get(col, "") for col in
                     ("Date", "Vendor", "Category", "Description", "Amount", "GST", "Currency", "Notes")]
                )
            zf.writestr("expenses.csv", csv_buf.getvalue())

            # every receipt file, named by date + vendor
            used_names = set()
            for e in expenses:
                v = e["values"]
                for att in e["attachments"]:
                    original_name, content = backend.download_attachment(att["id"])
                    ext = os.path.splitext(original_name)[1] or ""
                    base = f"{v.get('Date') or 'no-date'}_{_safe_name(str(v.get('Vendor') or 'unknown'))}"
                    name, n = f"receipts/{base}{ext}", 2
                    while name in used_names:
                        name = f"receipts/{base}-{n}{ext}"
                        n += 1
                    used_names.add(name)
                    zf.writestr(name, content)
    except Exception as exc:
        return render_template(
            "export.html",
            default_from=date_from or _financial_year_start(date.today()),
            default_to=date_to or date.today().isoformat(),
            error=f"Export failed: {exc}",
        )

    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"receipts_{date_from or 'all'}_to_{date_to or 'today'}.zip",
    )


if __name__ == "__main__":
    # host 0.0.0.0 lets you open the page from your phone on the same wifi
    app.run(host="0.0.0.0", port=5000, debug=True)
