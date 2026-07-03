# Receipt → Smartsheet Expense Tracker

Snap or upload a receipt (photo or PDF) from your phone and this app will:

1. **Read the receipt** using the Claude API (works on photos and PDFs, even slightly blurry ones)
2. **Extract the details** — date, vendor, category (Food / Accommodation / Travel / etc.), description, total amount, and GST (Australian rules: GST = total ÷ 11 when included but not itemised)
3. **Add a row to your Smartsheet** with all those details
4. **Attach the original receipt file to that row**, so the receipt is stored right next to its data

The expenses sheet ("Business Expenses") is created in your Smartsheet account **automatically** on the first upload — no manual sheet setup needed. The app is password-protected and installs on your phone's home screen like a normal app.

## Use it from your phone (recommended): deploy free on Render

### Step 1 — Get your two keys (5 min)

- **Claude API key**: sign up at [console.anthropic.com](https://console.anthropic.com) → *Billing* → add ~$5 credit → *API Keys* → *Create Key* → copy it (starts with `sk-ant-`).
- **Smartsheet token**: in Smartsheet, click your **profile picture** (bottom-left) → *Personal Settings* → *API Access* → *Generate new access token* → copy it.

### Step 2 — Deploy (5 min, no code)

1. Go to [render.com](https://render.com) and sign up — choose **"Sign in with GitHub"**.
2. Click **New +** → **Blueprint**.
3. Pick this repository, and select the branch that contains the app.
4. Render reads `render.yaml` and asks for three values:
   - `ANTHROPIC_API_KEY` — paste your Claude key
   - `SMARTSHEET_ACCESS_TOKEN` — paste your Smartsheet token
   - `APP_PASSWORD` — invent a password (this is what you'll type on your phone)
5. Click **Apply** and wait ~3 minutes. You'll get a link like `https://expense-tracker-xxxx.onrender.com`.

### Step 3 — Put it on your phone (1 min)

1. Open your Render link in your phone's browser and sign in with your password (it remembers you).
2. **iPhone**: tap the Share button → *Add to Home Screen*. **Android**: browser menu (⋮) → *Add to Home screen*.
3. You now have an "Expenses" app icon. Tap it → tap the upload box → *Take Photo* → done. The row appears in Smartsheet a few seconds later with the receipt attached.

> **Note on the free tier:** Render's free plan puts the app to sleep after ~15 idle minutes. The first upload after a break takes ~1 minute to wake up; after that it's fast. Render's $7/month plan removes the sleep if it bothers you.

## Or run it locally

```bash
cd expense-tracker
pip install -r requirements.txt
cp .env.example .env   # then fill in your keys
python app.py
```

Open [http://localhost:5000](http://localhost:5000) — or `http://<your-computer's-IP>:5000` from your phone on the same wifi.

## Costs

- Render: free tier works fine (with the wake-up delay noted above).
- Smartsheet API: free with your existing account.
- Claude API: a typical receipt costs a fraction of a cent to process.

## How it works

| File | What it does |
|---|---|
| `app.py` | Flask web app — login, upload page, and the glue between the other parts |
| `extractor.py` | Sends the receipt to Claude with a JSON schema (structured outputs), so the response is always valid, parseable data |
| `smartsheet_client.py` | Finds/creates the sheet, adds rows, attaches receipt files via the Smartsheet REST API |
| `setup_sheet.py` | Optional — pre-creates the sheet (the app does this automatically anyway) |
| `static/` | App icons + manifest so the page installs like a phone app |
| `../render.yaml` | One-click deployment recipe for Render |

Every extraction includes a **Notes** column entry when something was unclear (blurry total, missing date, guessed category), so you know which rows to double-check.
