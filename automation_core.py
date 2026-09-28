
"""
automation_core.py — GST Invoice Bulk Downloader (Air India Express & IndiGo)
=============================================================================
Reusable automation engine. Importable by the Streamlit UI and the CLI.
"""

from __future__ import annotations

import base64
import itertools
import logging
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import pandas as pd
from playwright.sync_api import (
    BrowserContext,
    Download,
    Page,
    sync_playwright,
    TimeoutError as PWTimeoutError,
)


# ---------------------------------------------------------------------------
# Configuration & Selectors
# ---------------------------------------------------------------------------

@dataclass
class AirIndiaExpressConfig:
    URL = "https://www.airindiaexpress.com/gst-tax-invoice"
    SEL_PNR = "input#pnr"
    SEL_ORIGIN = "input#Origin"
    SEL_SUBMIT = "div.search-button"
    SEL_ERROR = "text=No records present"

@dataclass
class IndiGoConfig:
    URL = "https://www.goindigo.in/view-gst-invoice.html"
    # Selectors confirmed from live site screenshots
    SEL_PNR    = "input[placeholder*='PNR' i], input[placeholder*='Booking Reference' i]"
    SEL_EMAIL  = "input[placeholder*='Email' i], input[placeholder*='GST Email' i]"
    SEL_SUBMIT = "button:has-text('Retrieve GST Invoice'), button:has-text('Get Invoice'), button[type='submit']"
    # Known IndiGo error message fragments (checked individually)
    ERROR_TEXTS = [
        "We are unable to fetch your GST Invoice",
        "No invoice found",
        "Invalid details",
        "No records",
        "Unable to process",
        "Sorry",
    ]

CAPTCHA_SELECTORS = [
    "iframe[src*='recaptcha']",
    "iframe[src*='captcha']",
    "div[class*='captcha']",
    "iframe[title*='challenge']",
    "iframe[title*='CAPTCHA']",
    "#challenge-form",
]

# ---------------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal Helpers
# ---------------------------------------------------------------------------

def _already_succeeded(output_dir: Path, pnr: str) -> bool:
    """Return True if this PNR folder exists and contains at least one PDF."""
    pnr_dir = output_dir / pnr
    if pnr_dir.exists() and pnr_dir.is_dir():
        pdfs = list(pnr_dir.glob("*.pdf"))
        return len(pdfs) > 0
    return False


def _is_captcha_present(page: Page) -> bool:
    """Detect if any known CAPTCHA elements are currently visible on the page."""
    for sel in CAPTCHA_SELECTORS:
        try:
            elem = page.query_selector(sel)
            if elem and elem.is_visible():
                return True
        except Exception:
            pass
    return False


def _wait_for_captcha_resolution(page: Page, poll_interval: float = 2.0, max_wait: float = 120.0) -> bool:
    """Pause execution and poll the page until the user manually resolves the CAPTCHA."""
    logger.warning("CAPTCHA detected — please solve it in the browser window.")
    deadline = time.time() + max_wait
    while time.time() < deadline:
        time.sleep(poll_interval)
        if not _is_captcha_present(page):
            logger.info("CAPTCHA resolved — continuing.")
            return True
    logger.error("CAPTCHA not solved in %.0f s — skipping.", max_wait)
    return False


# ---------------------------------------------------------------------------
# Air India Express Logic
# ---------------------------------------------------------------------------

def _fill_origin_with_autocomplete(page: Page, origin_code: str) -> bool:
    """Handle Air India Express's complex dropdown autocomplete input for the origin city."""
    origin = origin_code.strip()
    try:
        origin_input = page.locator(AirIndiaExpressConfig.SEL_ORIGIN).first
        origin_input.wait_for(state="visible", timeout=10_000)
        origin_input.click()
        origin_input.fill("")
        origin_input.type(origin, delay=120)
        page.wait_for_timeout(2000)

        clicked = False
        try:
            js_hit = page.evaluate(
                """() => {
                    const opt = document.querySelector('div.Source_List_Gst option');
                    if (opt) { opt.click(); return true; }
                    return false;
                }"""
            )
            if js_hit:
                page.wait_for_timeout(600)
                clicked = True
        except Exception:
            pass

        if not clicked:
            try:
                first_opt = page.locator("div.Source_List_Gst option").first
                if first_opt.count() > 0:
                    first_opt.click(force=True)
                    page.wait_for_timeout(600)
                    clicked = True
            except Exception:
                pass

        if not clicked:
            origin_input.press("ArrowDown")
            page.wait_for_timeout(300)
            page.keyboard.press("Enter")
            page.wait_for_timeout(500)

        page.wait_for_timeout(500)
        return True

    except PWTimeoutError:
        return False
    except Exception as exc:
        logger.error("Error filling origin: %s", exc)
        return False


def _process_air_india_express(page: Page, pnr: str, origin: str, output_dir: Path, context: BrowserContext, cb: Callable, idx: int) -> None:
    """Execute the full download flow for Air India Express."""
    try:
        page.goto(AirIndiaExpressConfig.URL, wait_until="domcontentloaded", timeout=30_000)
    except PWTimeoutError:
        cb(idx, pnr, origin, "ERROR", "Page load timed out")
        return

    page.wait_for_timeout(1500)

    if _is_captcha_present(page):
        if not _wait_for_captcha_resolution(page):
            cb(idx, pnr, origin, "CAPTCHA_TIMEOUT", "CAPTCHA not solved in time")
            return

    # Fill PNR
    try:
        pnr_input = page.locator(AirIndiaExpressConfig.SEL_PNR).first
        pnr_input.wait_for(state="visible", timeout=10_000)
        pnr_input.click()
        pnr_input.fill(pnr)
    except PWTimeoutError:
        cb(idx, pnr, origin, "ERROR", "PNR input not found")
        return

    # Fill Origin
    if not _fill_origin_with_autocomplete(page, origin):
        cb(idx, pnr, origin, "ERROR", "Origin input not found / autocomplete failed")
        return

    pnr_dir = output_dir / pnr
    pnr_dir.mkdir(parents=True, exist_ok=True)

    # Click Submit
    try:
        submit = page.locator(AirIndiaExpressConfig.SEL_SUBMIT).first
        submit.wait_for(state="visible", timeout=10_000)
        submit.click()
    except Exception as exc:
        cb(idx, pnr, origin, "ERROR", f"Submit error: {exc}")
        return

    page.wait_for_timeout(2000)

    # Check for errors
    try:
        if page.locator(AirIndiaExpressConfig.SEL_ERROR).first.is_visible(timeout=3_000):
            cb(idx, pnr, origin, "INVALID", "Site: No records present — wrong PNR or origin")
            return
    except Exception:
        pass

    if _is_captcha_present(page):
        if not _wait_for_captcha_resolution(page):
            cb(idx, pnr, origin, "CAPTCHA_TIMEOUT", "CAPTCHA after submit — not solved in time")
            return
        try:
            page.locator(AirIndiaExpressConfig.SEL_SUBMIT).first.click()
            page.wait_for_timeout(2000)
        except Exception:
            pass

    # Process results table
    dl_icon_selector = "img[alt='download icon']"
    try:
        page.locator(dl_icon_selector).first.wait_for(state="visible", timeout=15_000)
    except PWTimeoutError:
        cb(idx, pnr, origin, "ERROR", "Results table did not appear")
        return

    downloaded_count = 0
    rows = page.locator(f"tr:has({dl_icon_selector})").all()
    
    if not rows:
        cb(idx, pnr, origin, "ERROR", "No downloadable invoices found in table")
        return
        
    for i, row in enumerate(rows):
        try:
            invoice_td = row.locator("td").nth(5)
            invoice_num = invoice_td.inner_text().strip()
            dl_icon = row.locator(dl_icon_selector).first
            
            with page.expect_download(timeout=25_000) as dl_info:
                dl_icon.click()
            dl: Download = dl_info.value
            
            if invoice_num and invoice_num.isalnum():
                filename = f"{invoice_num}.pdf"
            else:
                suggested = dl.suggested_filename or f"invoice_{i+1}.pdf"
                filename = suggested if suggested.lower().endswith(".pdf") else f"{suggested}.pdf"
                
            out_path = pnr_dir / filename
            dl.save_as(str(out_path))
            downloaded_count += 1
        except Exception as exc:
            logger.error("Error downloading row %d for PNR %s: %s", i+1, pnr, exc)

    if downloaded_count > 0:
        cb(idx, pnr, origin, "SUCCESS", f"Saved {downloaded_count} invoice(s)")
    else:
        cb(idx, pnr, origin, "ERROR", "Failed to download invoices")


# ---------------------------------------------------------------------------
# IndiGo Logic
# ---------------------------------------------------------------------------

def _process_indigo(page: Page, pnr: str, email: str, output_dir: Path, context: BrowserContext, cb: Callable, idx: int) -> None:
    """
    Execute the full IndiGo download flow:
      1. Fill PNR / Booking Reference and GST Email ID
      2. Click ‘Retrieve GST Invoice’
      3. Click the Download icon on the results page (opens new tab)
      4. Save the invoice tab as PDF using CDP printToPDF (no print dialog)
    """
    try:
        page.goto(IndiGoConfig.URL, wait_until="domcontentloaded", timeout=30_000)
    except PWTimeoutError:
        cb(idx, pnr, email, "ERROR", "Page load timed out")
        return

    page.wait_for_timeout(2000)

    # Dismiss cookie consent — try "Accept All" first (ensures all cookies are set,
    # which IndiGo may require for their GST API to respond)
    for cookie_sel in [
        "button:has-text('Accept All')",
        "button:has-text('Accept Essential Only')",
    ]:
        try:
            btn = page.locator(cookie_sel).first
            if btn.is_visible(timeout=3000):
                btn.click()
                page.wait_for_timeout(800)
                break
        except Exception:
            pass

    if _is_captcha_present(page):
        if not _wait_for_captcha_resolution(page):
            cb(idx, pnr, email, "CAPTCHA_TIMEOUT", "CAPTCHA not solved in time")
            return

    # ── Fill PNR ─────────────────────────────────────────────────────────────
    # Confirmed from screenshot: placeholder="PNR / Booking Reference"
    # Use type() with delay to simulate real keystrokes — prevents bot detection
    try:
        pnr_input = page.locator(IndiGoConfig.SEL_PNR).first
        pnr_input.wait_for(state="visible", timeout=10_000)
        pnr_input.click()
        pnr_input.type(pnr, delay=80)   # 80ms per keystroke — looks human
    except PWTimeoutError:
        cb(idx, pnr, email, "ERROR", "PNR field not found on IndiGo page")
        return

    page.wait_for_timeout(500)  # brief pause between fields

    # ── Fill Email ────────────────────────────────────────────────────────────
    # Confirmed from screenshot: placeholder="GST Email ID"
    try:
        email_input = page.locator(IndiGoConfig.SEL_EMAIL).first
        email_input.wait_for(state="visible", timeout=10_000)
        email_input.click()
        email_input.type(email, delay=60)  # 60ms per keystroke
    except PWTimeoutError:
        cb(idx, pnr, email, "ERROR", "Email field not found on IndiGo page")
        return

    page.wait_for_timeout(400)  # brief pause before submitting

    pnr_dir = output_dir / pnr
    pnr_dir.mkdir(parents=True, exist_ok=True)
    # ── Submit Form ───────────────────────────────────────────────────────────
    # Confirmed from screenshot: button text = "Retrieve GST Invoice"
    try:
        submit = page.locator(IndiGoConfig.SEL_SUBMIT).first
        submit.wait_for(state="visible", timeout=10_000)
        submit.click()
    except Exception as exc:
        cb(idx, pnr, email, "ERROR", f"Submit button error: {exc}")
        return

    page.wait_for_timeout(3000)

    # Check for error messages on results page
    # IndiGo shows various error banners — check each one individually
    _UNAVAILABLE_HINT = "unable to fetch"
    for err_text in IndiGoConfig.ERROR_TEXTS:
        try:
            loc = page.locator(f":has-text('{err_text}')").last
            if loc.is_visible(timeout=1_500):
                visible_text = loc.inner_text().strip()[:200]
                if _UNAVAILABLE_HINT in visible_text.lower():
                    cb(idx, pnr, email, "UNAVAILABLE",
                       "IndiGo servers temporarily unable to fetch this invoice — try again later")
                else:
                    cb(idx, pnr, email, "INVALID",
                       f"IndiGo error: {visible_text}")
                return
        except Exception:
            pass

    if _is_captcha_present(page):
        if not _wait_for_captcha_resolution(page):
            cb(idx, pnr, email, "CAPTCHA_TIMEOUT", "CAPTCHA after submit")
            return

    # ── Click Download button ─────────────────────────────────────────────────
    # From screenshot: a download-arrow icon with "Download" text below it
    download_btn = None
    for sel in [
        "text=Download",
        "button:has-text('Download')",
        "a:has-text('Download')",
        "[aria-label*='Download' i]",
        "[title*='Download' i]",
        "[class*='download' i]",
    ]:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=4_000):
                download_btn = loc
                break
        except Exception:
            pass

    if download_btn is None:
        # Log page content for debugging
        page_text = page.inner_text("body")[:500]
        cb(idx, pnr, email, "ERROR", f"Download button not found. Page: {page_text}")
        return

    # ── Open invoice page and save as PDF ────────────────────────────────────
    # Clicking Download usually opens a new tab.
    download_btn.click()
    page.wait_for_timeout(3000)

    # Check if a new tab was actually opened
    invoice_page = page
    if len(context.pages) > 1:
        # The new tab is typically the last one in the context
        invoice_page = context.pages[-1]

    try:
        # Wait for the invoice page to be fully rendered
        invoice_page.wait_for_load_state("domcontentloaded", timeout=30_000)
        invoice_page.wait_for_timeout(2000)  # extra buffer for JS rendering
        
        # Hide the "Print Gst Invoice" button so it doesn't show up in our final PDF
        try:
            invoice_page.evaluate("""
                const btns = document.querySelectorAll('button, input[type="button"]');
                btns.forEach(b => { if(b.innerText.includes('Print')) b.style.display = 'none'; });
            """)
        except Exception:
            pass
            
    except Exception as exc:
        cb(idx, pnr, email, "ERROR", f"Invoice page did not load properly: {exc}")
        return

    # Verify invoice content is actually on the page before printing
    try:
        page_text = invoice_page.inner_text("body")
        if len(page_text.strip()) < 100:
            invoice_page.close()
            cb(idx, pnr, email, "ERROR", "Invoice page loaded but appears empty — try again")
            return
    except Exception:
        pass  # if we can't check, proceed anyway

    # Save invoice page as PDF using Chrome DevTools Protocol
    # (Page.printToPDF works in both headed and headless Chromium)
    try:
        # Determine the invoice number for the filename
        invoice_number = "invoice"
        try:
            import re
            page_text_for_name = invoice_page.inner_text("body")
            match = re.search(r"Number:\s*([A-Z0-9]+)", page_text_for_name, re.IGNORECASE)
            if match:
                invoice_number = match.group(1).strip()
        except Exception:
            pass
            
        out_path = pnr_dir / f"{invoice_number}.pdf"
        
        cdp = context.new_cdp_session(invoice_page)
        result = cdp.send("Page.printToPDF", {
            "printBackground": True,
            "paperWidth": 8.27,    # A4 width in inches
            "paperHeight": 11.69,  # A4 height in inches
            "marginTop": 0.4,
            "marginBottom": 0.4,
            "marginLeft": 0.4,
            "marginRight": 0.4,
        })
        pdf_bytes = base64.b64decode(result["data"])
        out_path.write_bytes(pdf_bytes)
        cb(idx, pnr, email, "SUCCESS", f"Saved invoice for {pnr}")
    except Exception as exc:
        cb(idx, pnr, email, "ERROR", f"PDF generation failed: {exc}")
    finally:
        try:
            if invoice_page and invoice_page != page:
                invoice_page.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_pnr_list(source: str | Path, pnr_col: str, secondary_col: str) -> pd.DataFrame:
    """Read an Excel file and return a cleaned DataFrame containing only the required columns."""
    df = pd.read_excel(source, dtype=str)
    missing = [c for c in (pnr_col, secondary_col) if c not in df.columns]
    if missing:
        raise ValueError(
            f"Column(s) not found: {missing}. Available: {list(df.columns)}"
        )
    df = df[[pnr_col, secondary_col]].copy()
    df[pnr_col] = df[pnr_col].str.strip().str.upper()
    df[secondary_col] = df[secondary_col].str.strip()
    df = df.dropna(subset=[pnr_col, secondary_col])
    df = df[(df[pnr_col] != "") & (df[secondary_col] != "")]
    return df.reset_index(drop=True)


def run_batch(
    df: pd.DataFrame,
    airline: str,
    pnr_col: str,
    secondary_col: str,
    output_dir: str | Path,
    headless: bool = False,
    min_delay: float = 4.0,
    max_delay: float = 9.0,
    limit: Optional[int] = None,
    progress_callback: Optional[Callable[[int, int, str, str, str, str], None]] = None,
) -> None:
    """
    Run the full batch download against a provided DataFrame.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = list(itertools.islice(df.iterrows(), limit) if limit else df.iterrows())
    total = len(rows)

    def _cb(idx: int, pnr: str, secondary: str, status: str, detail: str) -> None:
        if progress_callback:
            try:
                progress_callback(idx, total, pnr, secondary, status, detail)
            except Exception:
                pass
        logger.info("[%d/%d] Airline=%s PNR=%-10s Sec=%-15s %-15s %s",
                    idx + 1, total, airline, pnr, secondary[:15], status, detail)

    with sync_playwright() as pw:
        try:
            # Try to use Brave browser first
            browser = pw.chromium.launch(
                executable_path=r"C:\Users\Dnyanesh Pathak\AppData\Local\BraveSoftware\Brave-Browser\Application\brave.exe",
                headless=headless,
                ignore_default_args=["--enable-automation"],
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-popup-blocking",
                    "--no-sandbox",
                ],
            )
        except Exception:
            # Fallback to Chrome if Brave isn't found at that exact path
            try:
                browser = pw.chromium.launch(
                    channel="chrome",
                    headless=headless,
                    ignore_default_args=["--enable-automation"],
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--disable-popup-blocking",
                        "--no-sandbox",
                    ],
                )
            except Exception:
                # Fallback to MS Edge if Chrome isn't installed (Edge is always on Windows)
                browser = pw.chromium.launch(
                    channel="msedge",
                    headless=headless,
                    ignore_default_args=["--enable-automation"],
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--disable-popup-blocking",
                        "--no-sandbox",
                    ],
                )
        
        # Omit user_agent so it natively matches the browser channel exactly
        context = browser.new_context(
            accept_downloads=True,
            viewport={"width": 1366, "height": 768},
        )
        
        try:
            page = context.new_page()
            
            for idx, (_, row) in enumerate(rows):
                pnr = str(row[pnr_col]).strip().upper()
                secondary = str(row[secondary_col]).strip()

                if _already_succeeded(output_dir, pnr):
                    logger.info("Skipping PNR %s — PDF already exists in folder.", pnr)
                    _cb(idx, pnr, secondary, "SKIPPED", "PDF already exists in folder")
                    continue

                try:
                    if airline == "IndiGo":
                        _process_indigo(page, pnr, secondary, output_dir, context, _cb, idx)
                    else:
                        _process_air_india_express(page, pnr, secondary, output_dir, context, _cb, idx)
                except Exception as exc:
                    logger.exception("Unexpected error for PNR %s", pnr)
                    _cb(idx, pnr, secondary, "ERROR", str(exc))

                if idx < total - 1:
                    delay = random.uniform(min_delay, max_delay)
                    logger.info("Waiting %.1f s before next row …", delay)
                    time.sleep(delay)
        finally:
            try:
                page.close()
            except Exception:
                pass
            try:
                context.close()
                browser.close()
            except Exception:
                pass
