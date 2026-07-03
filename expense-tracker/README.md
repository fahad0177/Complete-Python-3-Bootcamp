# Receipt → Smartsheet Expense Tracker

Upload a photo or PDF of a receipt and this app will:

1. **Read the receipt** using the Claude API (works on photos and PDFs, even slightly blurry ones)
2. **Extract the details** — date, vendor, category (Food / Accommodation / Travel / etc.), description, total amount, and GST (Australian rules: GST = total ÷ 11 when included but not itemised)
3. **Add a row to your Smartsheet** with all those details
4. **Attach the original receipt file to that row**, so the receipt is stored right next to its data

## Setup (one time, ~10 minutes)

### 1. Install the dependencies

```bash
cd expense-tracker
pip install -r requirements.txt
```

### 2. Get your two API keys

- **Anthropic (Claude) API key** — sign up at [console.anthropic.com](https://console.anthropic.com), then create a key under *API Keys*.
- **Smartsheet access token** — in Smartsheet, click your profile picture → *Personal Settings* → *API Access* → *Generate new access token*.

### 3. Create your .env file

```bash
cp .env.example .env
```

Open `.env` and paste in both keys.

### 4. Create the expenses sheet

```bash
python setup_sheet.py
```

This creates a **"Business Expenses"** sheet in your Smartsheet account with the right columns (Vendor, Date, Category, Description, Amount, GST, Currency, Notes) and prints the sheet ID. Copy that ID into `.env` as `SMARTSHEET_SHEET_ID`.

(If you already have a sheet you want to use, just make sure it has columns with those exact names and put its ID in `.env` instead.)

## Using it

```bash
python app.py
```

Open [http://localhost:5000](http://localhost:5000), drop a receipt in, and a few seconds later the row is in your Smartsheet with the receipt attached.

**From your phone:** the app listens on your local network, so while it's running on your computer you can open `http://<your-computer's-IP>:5000` from your phone (same wifi) and upload receipt photos straight from the camera roll.

## Costs

- Smartsheet API: free with your existing account.
- Claude API: a typical receipt costs a fraction of a cent to process (one image + a small structured response).

## How it works

| File | What it does |
|---|---|
| `app.py` | Flask web app — the upload page and the glue between the other two parts |
| `extractor.py` | Sends the receipt to Claude with a JSON schema (structured outputs), so the response is always valid, parseable data |
| `smartsheet_client.py` | Adds the row and attaches the receipt file via the Smartsheet REST API |
| `setup_sheet.py` | One-time script that creates the sheet with the right columns |

Every extraction includes a **Notes** column entry when something was unclear (blurry total, missing date, guessed category), so you know which rows to double-check.
