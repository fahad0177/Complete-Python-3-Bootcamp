"""Expense tracker web app.

Upload a receipt (photo or PDF) -> Claude extracts the details ->
a row is added to your Smartsheet with the receipt file attached.

Works locally (python app.py) or deployed to a host like Render (gunicorn app:app).
If APP_PASSWORD is set, the app asks for it once per device and remembers you.
The expenses sheet is created in Smartsheet automatically on the first upload.
"""

import hashlib
import os

from dotenv import load_dotenv
from flask import Flask, redirect, render_template, request, session, url_for

from extractor import extract_receipt, IMAGE_MEDIA_TYPES
from smartsheet_client import SmartsheetClient, find_or_create_sheet

load_dotenv()

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB upload cap
# Stable secret so login cookies survive server restarts.
app.secret_key = hashlib.sha256(f"expense-tracker:{APP_PASSWORD}".encode()).digest()

ALLOWED_EXTENSIONS = set(IMAGE_MEDIA_TYPES) | {".pdf"}

_sheet_id = None  # cached after first lookup


def get_sheet() -> SmartsheetClient:
    """Return a client for the expenses sheet, creating the sheet if needed."""
    global _sheet_id
    token = os.environ["SMARTSHEET_ACCESS_TOKEN"]
    if _sheet_id is None:
        _sheet_id = os.environ.get("SMARTSHEET_SHEET_ID") or find_or_create_sheet(
            token, os.environ.get("SHEET_NAME", "Business Expenses")
        )
    return SmartsheetClient(token, _sheet_id)


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
    return render_template("index.html")


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
        sheet = get_sheet()
        row_id = sheet.add_expense_row(expense)
        sheet.attach_receipt(row_id, file.filename, file_bytes, mime_type_for(file.filename))
        sheet_url = sheet.sheet_url()
    except KeyError as exc:
        return render_template(
            "index.html",
            error=f"Missing environment variable {exc}. Check the app's settings.",
            expense=expense,
        )
    except Exception as exc:
        return render_template(
            "index.html",
            error=f"Extracted the receipt but could not save to Smartsheet: {exc}",
            expense=expense,
        )

    return render_template("index.html", expense=expense, sheet_url=sheet_url)


if __name__ == "__main__":
    # host 0.0.0.0 lets you open the page from your phone on the same wifi
    app.run(host="0.0.0.0", port=5000, debug=True)
