"""Reads a receipt (image or PDF) with the Claude API and returns structured expense data."""

import base64
import json
import os

import anthropic

CATEGORIES = [
    "Food",
    "Accommodation",
    "Travel",
    "Fuel",
    "Office Supplies",
    "Equipment",
    "Phone & Internet",
    "Other",
]

# JSON schema the model's response is forced to follow (structured outputs).
RECEIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "date": {
            "type": ["string", "null"],
            "description": "Receipt/transaction date in YYYY-MM-DD format, null if not visible",
        },
        "vendor": {
            "type": ["string", "null"],
            "description": "Business name on the receipt",
        },
        "category": {
            "type": "string",
            "enum": CATEGORIES,
            "description": "Best-fit expense category",
        },
        "description": {
            "type": "string",
            "description": "Short human-readable summary of what was purchased",
        },
        "total_amount": {
            "type": ["number", "null"],
            "description": "Total amount paid including GST",
        },
        "gst_amount": {
            "type": ["number", "null"],
            "description": (
                "GST shown on the receipt. If the receipt is a tax invoice from a "
                "GST-registered Australian business and GST is included but not itemised, "
                "compute it as total / 11. Null only if the receipt shows no GST at all."
            ),
        },
        "currency": {
            "type": "string",
            "description": "ISO currency code, e.g. AUD",
        },
        "confidence_notes": {
            "type": "string",
            "description": "Anything unclear or assumed (blurry text, missing fields, etc). Empty string if none.",
        },
    },
    "required": [
        "date",
        "vendor",
        "category",
        "description",
        "total_amount",
        "gst_amount",
        "currency",
        "confidence_notes",
    ],
    "additionalProperties": False,
}

IMAGE_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def _content_block(file_bytes: bytes, filename: str) -> dict:
    """Build the image or document content block for the API call."""
    ext = os.path.splitext(filename)[1].lower()
    data = base64.standard_b64encode(file_bytes).decode("utf-8")

    if ext == ".pdf":
        return {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": data},
        }
    if ext in IMAGE_MEDIA_TYPES:
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": IMAGE_MEDIA_TYPES[ext], "data": data},
        }
    raise ValueError(f"Unsupported file type: {ext} (use PDF, JPG, PNG, GIF or WebP)")


def extract_receipt(file_bytes: bytes, filename: str) -> dict:
    """Send the receipt to Claude and return the extracted fields as a dict."""
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment

    response = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=16000,
        thinking={"type": "adaptive"},
        output_config={"format": {"type": "json_schema", "schema": RECEIPT_SCHEMA}},
        messages=[
            {
                "role": "user",
                "content": [
                    _content_block(file_bytes, filename),
                    {
                        "type": "text",
                        "text": (
                            "This is a business expense receipt. Extract the details. "
                            "Assume Australian GST rules (GST is 10%, so GST = total / 11 "
                            "when included but not itemised on a tax invoice). "
                            "Dates on Australian receipts are day-first (DD/MM/YYYY)."
                        ),
                    },
                ],
            }
        ],
    )

    text = next(block.text for block in response.content if block.type == "text")
    return json.loads(text)
