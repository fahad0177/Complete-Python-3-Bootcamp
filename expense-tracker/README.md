# Receipt → Spreadsheet Expense Tracker

Snap or upload a receipt (photo or PDF) from your phone and this app will:

1. **Read the receipt** using the Claude API (works on photos and PDFs, even slightly blurry ones)
2. **Extract the details** — date, vendor, category (Food / Accommodation / Travel / etc.), description, total amount, and GST (Australian rules: GST = total ÷ 11 when included but not itemised)
3. **Add a row to your expense sheet** with all those details
4. **Store the original receipt** next to its data — in Google Drive with a clickable link on the row (Google backend), or attached directly to the row (Smartsheet backend)
5. **Keep the sheet sorted by receipt date** (oldest first) — upload receipts in any order and they land in the right place
6. **Bundle everything up for tax time** — the "Download receipts" page gives you one ZIP with every receipt in a date range (named `2026-06-15_Officeworks.pdf` style) plus a CSV summary, defaulting to the current Australian financial year

## Storage options

| Backend | Cost | How receipts are stored |
|---|---|---|
| **Google Sheets + Drive** (default) | Free with any Gmail account | Receipts in a Drive folder, link on each row. The sheet exports to Excel anytime (*File → Download → .xlsx*) |
| **Smartsheet** | Needs a plan with API access (Pro+) | Receipt attached directly to each row |

The app picks Google when `GOOGLE_SERVICE_ACCOUNT_JSON` is set, otherwise Smartsheet when `SMARTSHEET_ACCESS_TOKEN` is set. The spreadsheet ("Business Expenses") and receipts folder are created **automatically on first upload** — no manual setup.

## Setup for the free Google backend

### Step 1 — Claude API key (~5 min)

Sign up at [console.anthropic.com](https://console.anthropic.com) → *Billing* → add ~$5 credit → *API Keys* → *Create Key* → copy it (starts with `sk-ant-`).

### Step 2 — Google OAuth app (~10 min, all clicking)

This lets the expense tracker act as *your own* Google account (for its own files only — the narrow `drive.file` scope), so the sheet and receipts live in your own Drive with your storage.

1. Go to [console.cloud.google.com](https://console.cloud.google.com), sign in with your Gmail, and create/select a project (e.g. `expense-tracker`).
2. Search **"Google Sheets API"** → **Enable**. Search **"Google Drive API"** → **Enable**.
3. Search **"OAuth consent screen"** (a.k.a. *Google Auth Platform*) → if it's your first time, click **Get started**: app name `Expense Tracker`, your email for both contacts, **Audience: External** → finish.
4. On the **Audience** page, click **Publish app** (moves it out of Testing — important, otherwise the connection expires every 7 days).
5. Search **"Credentials"** → **Create credentials** → **OAuth client ID** → Application type **Web application** → under **Authorized redirect URIs** add:
   `https://<your-app>.onrender.com/google/callback`
6. **Create** → copy the **Client ID** and **Client secret**.

### Step 3 — Deploy free on Render (~5 min)

1. Go to [render.com](https://render.com) → sign up with **"Sign in with GitHub"**.
2. **New +** → **Blueprint** → pick this repository and the branch containing the app.
3. Fill in the values Render asks for:
   - `ANTHROPIC_API_KEY` — from step 1
   - `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` — from step 2
   - `APP_PASSWORD` — invent a password for the app
4. **Apply** → wait ~3 minutes → you get a link like `https://expense-tracker-xxxx.onrender.com`. (If you didn't know this URL back in step 2.5, add the redirect URI now.)

### Step 4 — Connect your Google Drive (~1 min, one time)

1. Open your app link, sign in with your password, and tap **Connect Google Drive**.
2. Approve on Google's screen. You're immediately ready to upload.
3. The app then shows a **connection key** — copy it, and in Render go to your service → **Environment** → add `GOOGLE_REFRESH_TOKEN` = that key → **Save**. This makes the connection permanent across restarts.

### Step 5 — Phone (~1 min)

Open the link on your phone, sign in, then **iPhone**: Share → *Add to Home Screen* / **Android**: menu (⋮) → *Add to Home screen*. On your first upload the app creates a **"Business Expenses"** sheet and a **"Business Expense Receipts"** folder right in your own Google Drive.

> **Free-tier note:** Render's free plan puts the app to sleep after ~15 idle minutes; the first upload after a break takes ~1 minute to wake. Their $7/month plan removes this — optional.

## Or run it locally

```bash
cd expense-tracker
pip install -r requirements.txt
cp .env.example .env   # then fill in your keys
python app.py
```

Open [http://localhost:5000](http://localhost:5000) — or `http://<your-computer's-IP>:5000` from your phone on the same wifi.

## Costs

- Google Sheets/Drive: free (Drive's free 15 GB holds tens of thousands of receipt photos).
- Render: free tier works fine (with the wake-up delay noted above).
- Claude API: a typical receipt costs a few cents at most to process.

## How it works

| File | What it does |
|---|---|
| `app.py` | Flask web app — login, upload page, export, backend selection |
| `extractor.py` | Sends the receipt to Claude with a JSON schema (structured outputs), so the response is always valid, parseable data |
| `google_client.py` | Free backend — Google Sheet for rows, Drive folder for receipt files |
| `smartsheet_client.py` | Smartsheet backend — rows with receipts attached directly |
| `static/` | App icons + manifest so the page installs like a phone app |
| `../render.yaml` | One-click deployment recipe for Render |

Every extraction includes a **Notes** column entry when something was unclear (blurry total, missing date, guessed category), so you know which rows to double-check.
