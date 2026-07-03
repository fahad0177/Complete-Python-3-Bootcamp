"""Expense tracker web app.

Upload a receipt (photo or PDF) -> Claude extracts the details ->
a row is added to your Smartsheet with the receipt file attached.

Run with:  python app.py   then open http://localhost:5000
"""

import os

from dotenv import load_dotenv
from flask import Flask, render_template, request

from extractor import extract_receipt, IMAGE_MEDIA_TYPES
from smartsheet_client import SmartsheetClient

load_dotenv()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB upload cap

ALLOWED_EXTENSIONS = set(IMAGE_MEDIA_TYPES) | {".pdf"}


def mime_type_for(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    return "application/pdf" if ext == ".pdf" else IMAGE_MEDIA_TYPES[ext]


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
        sheet = SmartsheetClient(
            os.environ["SMARTSHEET_ACCESS_TOKEN"],
            os.environ["SMARTSHEET_SHEET_ID"],
        )
        row_id = sheet.add_expense_row(expense)
        sheet.attach_receipt(row_id, file.filename, file_bytes, mime_type_for(file.filename))
        sheet_url = sheet.sheet_url()
    except KeyError as exc:
        return render_template(
            "index.html",
            error=f"Missing environment variable {exc}. Check your .env file.",
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
