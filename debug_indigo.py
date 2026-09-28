"""
debug_indigo.py - Step-by-step debug tester for IndiGo GST invoice flow.
"""
import base64, sys
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

TEST_PNR   = "T6PM8V"
TEST_EMAIL = "argst@amararaja.com"
OUTPUT_DIR = Path(r"C:\Users\Dnyanesh Pathak\OneDrive\Desktop\INVOICES")
SHOTS_DIR  = OUTPUT_DIR / "_debug_screenshots"
SHOTS_DIR.mkdir(parents=True, exist_ok=True)

step = 0
def shot(page, label):
    global step; step += 1
    p = SHOTS_DIR / f"{step:02d}_{label}.png"
    page.screenshot(path=str(p), full_page=True)
    print(f"  Screenshot: {p.name}")

def log(msg): print(f"\n{'='*55}\n  {msg}\n{'='*55}")

INDIGO_URL = "https://www.goindigo.in/view-gst-invoice.html"
SEL_PNR    = "input[placeholder*='PNR' i], input[placeholder*='Booking Reference' i]"
SEL_EMAIL  = "input[placeholder*='Email' i], input[placeholder*='GST Email' i]"
SEL_SUBMIT = "button:has-text('Retrieve GST Invoice'), button:has-text('Get Invoice'), button[type='submit']"

with sync_playwright() as pw:
    log("STEP 1: Launching Chrome")
    try:
        browser = pw.chromium.launch(channel="chrome", headless=False,
            ignore_default_args=["--enable-automation"],
            args=["--disable-blink-features=AutomationControlled","--no-sandbox"])
        print("  Chrome launched OK")
    except Exception as e:
        print(f"  Chrome failed ({e}), trying Edge")
        browser = pw.chromium.launch(channel="msedge", headless=False,
            ignore_default_args=["--enable-automation"],
            args=["--disable-blink-features=AutomationControlled","--no-sandbox"])
        print("  Edge launched OK")

    context = browser.new_context(accept_downloads=True, viewport={"width":1366,"height":768})
    page = context.new_page()

    log("STEP 2: Navigate to IndiGo page")
    page.goto(INDIGO_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2000)
    shot(page, "initial_page")

    log("STEP 3: Accept cookies")
    for sel in ["button:has-text('Accept All')", "button:has-text('Accept Essential Only')"]:
        try:
            b = page.locator(sel).first
            if b.is_visible(timeout=3000):
                b.click(); page.wait_for_timeout(1000)
                print(f"  Clicked: {sel}"); break
        except: pass
    else:
        print("  No cookie banner found")
    shot(page, "after_cookie")

    log("STEP 4: All inputs on page")
    info = page.evaluate("""() => Array.from(document.querySelectorAll('input')).map(i =>
        `  type=${i.type} id="${i.id}" name="${i.name}" placeholder="${i.placeholder}"`).join('\\n')""")
    print(info or "  (none found)")

    log(f"STEP 5: Fill PNR = {TEST_PNR}")
    try:
        pnr_el = page.locator(SEL_PNR).first
        pnr_el.wait_for(state="visible", timeout=10000)
        pnr_el.click(); pnr_el.type(TEST_PNR, delay=80)
        print(f"  PNR field value: '{pnr_el.input_value()}'")
    except PWTimeoutError:
        print(f"  ERROR: PNR input not found"); shot(page,"pnr_error"); sys.exit(1)
    page.wait_for_timeout(500); shot(page,"after_pnr")

    log(f"STEP 6: Fill Email = {TEST_EMAIL}")
    try:
        em_el = page.locator(SEL_EMAIL).first
        em_el.wait_for(state="visible", timeout=10000)
        em_el.click(); em_el.type(TEST_EMAIL, delay=60)
        print(f"  Email field value: '{em_el.input_value()}'")
    except PWTimeoutError:
        print(f"  ERROR: Email input not found"); shot(page,"email_error"); sys.exit(1)
    page.wait_for_timeout(400); shot(page,"after_email")

    log("STEP 7: Click Submit")
    try:
        sub = page.locator(SEL_SUBMIT).first
        sub.wait_for(state="visible", timeout=10000)
        print(f"  Button text: '{sub.inner_text()}'")
        sub.click(); print("  Clicked submit")
    except Exception as e:
        print(f"  ERROR: {e}"); shot(page,"submit_error"); sys.exit(1)
    page.wait_for_timeout(4000); shot(page,"after_submit")

    log("STEP 8: Page content after submit")
    body = page.inner_text("body").strip()[:800]
    print(body)

    log("STEP 9: Find Download button")
    dl_btn = None
    for sel in ["text=Download","button:has-text('Download')","a:has-text('Download')",
                "[aria-label*='Download' i]","[title*='Download' i]","[class*='download' i]"]:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=4000):
                dl_btn = loc
                print(f"  FOUND: selector='{sel}' tag={loc.evaluate('el=>el.tagName')}")
                break
        except: pass
    if dl_btn is None:
        print("  ERROR: Download button not found! Clickable elements:")
        print(page.evaluate("""() => Array.from(document.querySelectorAll('a,button,[onclick]')).map(
            el=>`  ${el.tagName} | class="${el.className}" | text="${el.innerText.trim().slice(0,60)}"`).join('\\n')""")[:3000])
        shot(page,"dl_not_found"); sys.exit(1)

    log("STEP 10: Click Download, wait for popup tab")
    try:
        with context.expect_page(timeout=15000) as popup_info:
            dl_btn.click()
        inv = popup_info.value
        inv.wait_for_load_state("networkidle", timeout=30000)
        inv.wait_for_timeout(2000)
        print(f"  Invoice page URL: {inv.url}")
        shot(inv,"invoice_page")
    except Exception as e:
        print(f"  ERROR: Popup not opened: {e}"); shot(page,"popup_failed"); sys.exit(1)

    log("STEP 11: Save as PDF via CDP")
    out_path = OUTPUT_DIR / TEST_PNR / "invoice_debug.pdf"
    (OUTPUT_DIR / TEST_PNR).mkdir(parents=True, exist_ok=True)
    try:
        cdp = context.new_cdp_session(inv)
        result = cdp.send("Page.printToPDF", {"printBackground":True,
            "paperWidth":8.27,"paperHeight":11.69,
            "marginTop":0.4,"marginBottom":0.4,"marginLeft":0.4,"marginRight":0.4})
        pdf = base64.b64decode(result["data"])
        out_path.write_bytes(pdf)
        print(f"  PDF saved! {len(pdf):,} bytes -> {out_path}")
    except Exception as e:
        print(f"  ERROR saving PDF: {e}"); shot(inv,"pdf_error")
    finally:
        try: inv.close()
        except: pass

    log(f"DONE! Check:\n  PDF: {out_path}\n  Screenshots: {SHOTS_DIR}")
    input("\n  Press Enter to close the browser...")
    browser.close()
