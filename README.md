# IndiGo GST Invoice Bulk Downloader

A **100% local** automation tool that bulk-downloads IndiGo GST invoice PDFs.
You supply an Excel file of PNR + Email pairs; the tool fills the form on
[goindigo.in/view-gst-invoice.html](https://www.goindigo.in/view-gst-invoice.html)
for each row and saves the resulting PDF to a folder you choose.

> ⚠️ **Terms of Service notice:** Automated access to IndiGo's website may
> fall outside their Terms of Service. Use this tool only for your own
> bookings and at your own discretion.

---

## Table of Contents
1. [Prerequisites](#prerequisites)
2. [Installation](#installation)
3. [Quick-start: Streamlit UI](#quick-start-streamlit-ui)
4. [Quick-start: CLI](#quick-start-cli)
5. [Excel file format](#excel-file-format)
6. [Resume / skip behaviour](#resume--skip-behaviour)
7. [CAPTCHA handling](#captcha-handling)
8. [Rate-limiting rationale](#rate-limiting-rationale)
9. [Updating selectors (playwright codegen)](#updating-selectors-playwright-codegen)
10. [File structure](#file-structure)
11. [Troubleshooting](#troubleshooting)

---

## Prerequisites

- Python 3.10 or later
- Windows / macOS / Linux

---

## Installation

```bash
# 1. Clone or download this folder, then open a terminal inside it

# 2. (Optional but recommended) create a virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Install Chromium for Playwright (one-time, ~180 MB)
playwright install chromium
```

---

## Quick-start: Streamlit UI

```bash
streamlit run streamlit_app.py
```

> ⚠️ **LOCAL USE ONLY** — Do **not** deploy this to Streamlit Community Cloud,
> Heroku, or any other hosted service. The app must run on `localhost` and
> makes direct browser requests to `goindigo.in` on your behalf.

The browser will open automatically at `http://localhost:8501`. Steps:

1. **Upload** your `.xlsx` file (drag & drop).
2. **Map columns** — the app auto-guesses the PNR and Email columns from
   header names; adjust if needed.
3. **Set output folder** — default is `~/IndiGo_Invoices`.
4. **Options:**
   - **Headless** — hides the browser window. Keep this **OFF** if you expect
     CAPTCHAs (you need to see and solve them).
   - **Test mode** — runs only the first PNR so you can verify one row works
     before committing to the full batch.
5. Click **Start Download** and watch the live progress table.
6. Click **Open Output Folder** when done.

---

## Quick-start: CLI

Edit the `CONFIG` block at the top of `cli.py`:

```python
EXCEL_PATH = r"C:\path\to\bookings.xlsx"
PNR_COL    = "PNR"          # exact column header in your file
EMAIL_COL  = "Email"
OUTPUT_DIR = r"C:\IndiGo_Invoices"
HEADLESS   = False           # keep False if CAPTCHAs expected
LIMIT      = 1               # set None to run all rows
```

Then run:

```bash
python cli.py
```

---

## Excel file format

The file must be `.xlsx` and contain at least two columns. Column names are
flexible — you pick them in the UI or set them in the CLI config. Example:

| PNR    | Email              |
|--------|--------------------|
| ABCDEF | user@example.com   |
| GHIJKL | another@example.com|

- PNR values are automatically uppercased and stripped of whitespace.
- Email values are stripped of whitespace.
- Rows where PNR or Email is blank are skipped.

---

## Resume / skip behaviour

After each row the tool appends a line to `download_log.csv` in the output
folder:

```
timestamp,pnr,status,detail
2026-08-23 10:05:12,ABCDEF,SUCCESS,Saved to C:\...\ABCDEF.pdf
2026-08-23 10:05:24,GHIJKL,ERROR,Site: incorrect details
```

On the **next run with the same log file**, any PNR that already has a
`SUCCESS` entry is automatically skipped — you can safely stop (`Ctrl+C` in
CLI, close the browser tab in UI) and re-run without re-downloading.

---

## CAPTCHA handling

IndiGo uses Akamai Bot Manager and may occasionally show a CAPTCHA challenge.

**What happens:**
1. The automation detects the CAPTCHA (iframe or challenge-form element).
2. It pauses and waits up to **2 minutes** for you to solve it manually in
   the visible browser window.
3. Once the CAPTCHA is gone, automation continues automatically.
4. If the CAPTCHA is not solved in 2 minutes, the row is logged as
   `CAPTCHA_TIMEOUT` and the batch moves on.

**Important:** Keep **Headless mode OFF** (the default) so you can see and
solve CAPTCHAs. A headless browser cannot display a CAPTCHA for you to solve.

---

## Rate-limiting rationale

A random delay of **4–9 seconds** (configurable) is inserted between each row.
This mimics natural human browsing speed and significantly reduces the risk of
IndiGo's bot-detection systems blocking your requests. **Do not set this below
~3 seconds.** The delay is non-negotiable for robust operation.

---

## Updating selectors (playwright codegen)

IndiGo's website is a JavaScript SPA. If the form selectors change after a
site update, automation will break. To rediscover the real selectors:

```bash
playwright codegen https://www.goindigo.in/view-gst-invoice.html
```

This opens a browser with a code-recorder sidebar. Click the PNR field, the
Email field, and the Submit button — the recorder shows the exact selectors.
Update the `SEL_*` constants at the top of `automation_core.py` accordingly.

**Current selectors (verified 2026-08-23):**

| Field          | Selector                                          |
|----------------|---------------------------------------------------|
| PNR input      | `input[placeholder='PNR / Booking Reference']`    |
| Email input    | `input[placeholder='GST Email ID']`               |
| Submit button  | `button:has-text('Retrieve GST Invoice')`         |
| PNR tab        | `button:has-text('PNR')`                          |
| Error toast    | `text=The details entered by you seems to be incorrect` |
| Download btn   | `button:has-text('Download Invoice')` *(TODO — verify with a live PNR)* |

---

## File structure

```
Invoice_downloader/
├── automation_core.py   # Core automation engine (importable)
├── streamlit_app.py     # Streamlit browser UI
├── cli.py               # CLI fallback (edit config & run)
├── requirements.txt     # Python dependencies
└── README.md            # This file
```

Output folder (default `~/IndiGo_Invoices/`):
```
IndiGo_Invoices/
├── ABCDEF.pdf           # Invoice PDF named by PNR
├── GHIJKL.pdf
└── download_log.csv     # Run log (used for resume/skip)
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `ModuleNotFoundError: playwright` | Not installed | `pip install playwright` |
| Browser does not open | Chromium not installed | `playwright install chromium` |
| `Column not found` error | Wrong column name | Check exact header names in your Excel file |
| Row always errors with "selector not found" | Site redesign | Run `playwright codegen` to get new selectors |
| PDF is 0 bytes or corrupt | New-tab PDF fetch issue | Leave headless OFF; check console for JS errors |
| Stuck on CAPTCHA forever | Headless ON | Turn headless OFF and solve manually |
| Rate-limited / blocked | Delay too short | Increase `MAX_DELAY` |
