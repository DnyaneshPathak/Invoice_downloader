"""
cli.py — GST Invoice Downloader (CLI fallback)
==============================================
Edit the CONFIG block below and run:
    python cli.py
"""

from pathlib import Path
from automation_core import load_pnr_list, run_batch

# ===========================================================================
# ── CONFIG — edit these values before running ──────────────────────────────
# ===========================================================================

# Which airline to download from?
# Options: "Air India Express", "IndiGo"
AIRLINE: str = "Air India Express"

# Path to your Excel file (must contain PNR + Secondary column)
EXCEL_PATH: str = r"C:\path\to\your\bookings.xlsx"

# Exact column header names in your Excel file
PNR_COL:       str = "PNR"
SECONDARY_COL: str = "Origin City"  # For IndiGo, change this to "Email" or similar

# Root output folder — each PNR gets its own sub-folder:
#   OUTPUT_DIR\{PNR}\invoice.pdf
OUTPUT_DIR: str = r"C:\Users\Dnyanesh Pathak\OneDrive\Desktop\INVOICES"

# Show the browser? Keep False=visible to handle CAPTCHAs manually.
HEADLESS: bool = False

# Random delay between rows (seconds). Do NOT set below 3 s.
MIN_DELAY: float = 4.0
MAX_DELAY: float = 9.0

# Limit to first N rows (None = all rows)
LIMIT = None   # e.g. LIMIT = 1 for a single-PNR test

# ===========================================================================


def main() -> None:
    print("=" * 60)
    print(f"  {AIRLINE} GST Invoice Downloader — CLI")
    print("=" * 60)

    print(f"\nLoading Excel: {EXCEL_PATH}")
    try:
        df = load_pnr_list(EXCEL_PATH, PNR_COL, SECONDARY_COL)
    except Exception as e:
        print(f"\n[ERROR] Could not load Excel file:\n  {e}")
        return

    n = len(df) if LIMIT is None else min(LIMIT, len(df))
    print(f"Rows to process: {n}  |  Output root: {OUTPUT_DIR}\n")
    print(f"Each PDF will be saved to: {OUTPUT_DIR}\\{{PNR}}\\invoice.pdf\n")

    def progress(idx, total, pnr, secondary, status, detail):
        bar = ("█" * int((idx + 1) / total * 28)).ljust(28)
        print(f"\r  [{bar}] {idx+1}/{total}  {pnr:<10} {secondary:<15} {status:<18}",
              end="", flush=True)

    try:
        run_batch(
            df=df,
            airline=AIRLINE,
            pnr_col=PNR_COL,
            secondary_col=SECONDARY_COL,
            output_dir=OUTPUT_DIR,
            headless=HEADLESS,
            min_delay=MIN_DELAY,
            max_delay=MAX_DELAY,
            limit=LIMIT,
            progress_callback=progress,
        )
        print(f"\n\nDone. PDFs saved under: {OUTPUT_DIR}")
    except KeyboardInterrupt:
        print("\n\nInterrupted. Re-run to resume — already downloaded PDFs will be skipped.")
    except Exception as e:
        print(f"\n[ERROR] {e}")


if __name__ == "__main__":
    main()
