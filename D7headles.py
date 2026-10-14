import os
import csv
import time
import base64
import shutil
import logging
import tempfile
import threading
import multiprocessing as mp
from queue import Empty

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# ================= CONFIG =================

WORKERS = 4               # number of INDEPENDENT Chrome browser processes running in parallel
WAIT = 25
WAIT_SHORT = 10
PDF_MIN_SIZE = 5000

MAX_RETRIES = 1           # retries per PNR before giving up
TASK_TIMEOUT = 90         # hard ceiling (seconds) for one PNR before we force-kill the driver
RESTART_AFTER = 25        # recycle the browser after this many PNRs to stop memory/slowdown creep
RESUME = True             # if True, skip PNRs already present in output.csv / pnr_errors.csv

# If all WORKERS browsers are genuinely running but only one *looks* like
# it's progressing, it's almost always resource contention -- N full,
# visible, image-rendering Chrome windows fighting over the same CPU/RAM.
# These two settings fix that directly:
BLOCK_IMAGES = True       # skip loading images on the fast search/lookup pages for speed.
                          # This is applied dynamically per-page (via CDP), and is always
                          # turned OFF again before the actual invoice page that gets printed
                          # to PDF, so the IndiGo logo is never missing from downloaded PDFs.
HEADLESS = False          # True = no visible windows, much lighter, but a few sites can detect
                          # headless Chrome and block it. Leave False first; if the site doesn't
                          # mind, flip to True for a large jump in how many can truly run at once.
STAGGER_START = 3         # seconds between launching each worker's browser, so N Chrome startups
                          # don't all spike the CPU at the exact same instant

# ---- NEW: keeps the multiple Chrome windows from stealing your mouse/
# keyboard focus or covering your screen while the script runs, so you can
# keep working normally on your machine. This is independent of HEADLESS --
# the windows still fully load/render (nothing about scraping or PDF
# printing changes), they are just placed off the visible desktop area
# instead of popping up on top of whatever you're doing. ----
KEEP_WINDOWS_OUT_OF_THE_WAY = True   # set False to restore old on-screen behavior
OFFSCREEN_POSITION = (-32000, -32000)  # far outside any real monitor
WINDOW_SIZE = "1366,768"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INVOICE_DIR = os.path.join(BASE_DIR, "invoices")
os.makedirs(INVOICE_DIR, exist_ok=True)

INPUT_FILE = os.path.join(BASE_DIR, "pnr.csv")
OUTPUT_FILE = os.path.join(BASE_DIR, "output.csv")
ERROR_FILE = os.path.join(BASE_DIR, "pnr_errors.csv")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("indigo")


# ================= CSV =================
# A csv_lock is created per-run via mp.Lock() and passed into each worker
# process explicitly, because a plain threading.Lock() cannot be shared
# across separate OS processes.

def init_files():
    with open(OUTPUT_FILE, "w", newline="") as f:
        csv.writer(f).writerow(["PNR", "INVOICES"])
    with open(ERROR_FILE, "w", newline="") as f:
        csv.writer(f).writerow(["PNR", "ERROR", "TIME"])


def load_done_pnrs():
    done = set()
    for path in (OUTPUT_FILE, ERROR_FILE):
        if os.path.exists(path):
            with open(path, newline="") as f:
                rows = list(csv.reader(f))[1:]
                for r in rows:
                    if r:
                        done.add(r[0].strip())
    return done


def write_output(csv_lock, pnr, invs):
    with csv_lock:
        with open(OUTPUT_FILE, "a", newline="") as f:
            csv.writer(f).writerow([pnr, ",".join(invs)])


def write_error(csv_lock, pnr, msg):
    with csv_lock:
        with open(ERROR_FILE, "a", newline="") as f:
            csv.writer(f).writerow([pnr, msg, time.strftime("%H:%M:%S")])


# ================= DRIVER =================

def create_driver(wid):
    """
    Each call creates a fully independent Chrome browser: its own isolated
    profile directory (the #1 cause of "only one browser actually works,
    the rest hang/reuse the first window") and its own driver process.
    Because each worker now lives in its own OS process (not a thread),
    undetected_chromedriver's internal state is never shared -- eliminating
    the freezing/serialization you were seeing.
    """
    options = uc.ChromeOptions()
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument(f"--window-size={WINDOW_SIZE}")
    options.page_load_strategy = "eager"

    # Lighten each browser's footprint so more of them can genuinely run
    # at once instead of starving each other for CPU/RAM/network.
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-background-networking")
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-backgrounding-occluded-windows")
    options.add_argument("--disable-renderer-backgrounding")
    options.add_argument("--disable-sync")
    options.add_argument("--disable-translate")
    options.add_argument("--mute-audio")
    options.add_argument("--metrics-recording-only")

    if HEADLESS:
        options.add_argument("--headless=new")

    # Launch the window off the visible desktop area right from the start
    # (rather than only minimizing after the fact) so it never flashes on
    # top of whatever you're doing and never grabs focus. Skipped when
    # HEADLESS is already on, since there's no window to place anyway.
    if KEEP_WINDOWS_OUT_OF_THE_WAY and not HEADLESS:
        x, y = OFFSCREEN_POSITION
        options.add_argument(f"--window-position={x},{y}")

    # Unique, isolated profile per worker -- prevents Chrome from treating
    # a second launch as "already running" and silently reusing/blocking
    # on the first instance.
    profile_dir = tempfile.mkdtemp(prefix=f"uc_profile_w{wid}_")
    options.add_argument(f"--user-data-dir={profile_dir}")

    last_err = None
    for attempt in range(1, 4):
        try:
            # NOTE: this Chrome() call / its arguments are left exactly as given.
            driver = uc.Chrome(
                options=options,
                version_main=152,
                use_subprocess=False
            )
            driver.set_page_load_timeout(WAIT)
            driver.set_script_timeout(WAIT)
            driver._uc_profile_dir = profile_dir  # stash for cleanup later

            # Belt-and-braces: some Chrome/driver combinations ignore the
            # --window-position launch flag, so re-assert the off-screen
            # position (and minimize) here too. Wrapped in try/except since
            # this is a convenience, not something that should ever fail a
            # task if the window manager rejects it.
            if KEEP_WINDOWS_OUT_OF_THE_WAY and not HEADLESS:
                try:
                    x, y = OFFSCREEN_POSITION
                    driver.set_window_position(x, y)
                    driver.minimize_window()
                except Exception:
                    pass

            # Enables dynamic per-navigation image blocking (see
            # set_image_blocking below) -- lets us block images on the fast
            # search/lookup pages for speed, then explicitly re-enable them
            # before the actual invoice page so the IndiGo logo is always
            # present in the printed PDF.
            try:
                driver.execute_cdp_cmd("Network.enable", {})
            except Exception:
                pass

            return driver
        except Exception as e:
            last_err = e
            log.warning(f"[WORKER {wid}] Driver creation attempt {attempt}/3 failed: {e}")
            time.sleep(3)

    raise RuntimeError(f"[WORKER {wid}] Could not create Chrome driver after 3 attempts: {last_err}")


def quit_driver(driver):
    profile_dir = getattr(driver, "_uc_profile_dir", None)
    try:
        driver.quit()
    except Exception:
        pass
    if profile_dir:
        shutil.rmtree(profile_dir, ignore_errors=True)


def wait_ready(driver, timeout=WAIT_SHORT):
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
    except Exception:
        pass


def set_image_blocking(driver, block: bool):
    """
    Toggles image loading at the network level for THIS driver, for
    whatever page is navigated to next. Used to block images on the fast
    PNR search/lookup pages (speed win, no visuals needed there) while
    always re-enabling them before the invoice page that actually gets
    printed to PDF -- so the IndiGo logo is never missing.
    """
    try:
        patterns = ["*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp", "*.svg", "*.ico"] if block else []
        driver.execute_cdp_cmd("Network.setBlockedURLs", {"urls": patterns})
    except Exception:
        pass


def wait_images_loaded(driver, timeout=WAIT_SHORT):
    """Confirms every <img> on the page (e.g. the IndiGo logo) has actually
    finished downloading before we print the page to PDF -- readyState
    'complete' alone doesn't always guarantee image bytes have arrived."""
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script(
                "return Array.from(document.images).every(img => img.complete && img.naturalWidth > 0)"
            )
        )
    except Exception:
        pass


# ================= FETCH =================

def fetch_invoices(driver, pnr, email, wid="?"):

    wlog = logging.getLogger(f"indigo.w{wid}")

    wlog.info(f"[WORKER {wid}] {pnr}: opening PNR lookup page")
    set_image_blocking(driver, BLOCK_IMAGES)
    driver.get("https://www.goindigo.in/view-gst-invoice.html/1000.html")
    wait_ready(driver)

    wait = WebDriverWait(driver, WAIT)

    pnr_in = wait.until(
        EC.presence_of_element_located((By.XPATH, '//input[contains(@placeholder,"PNR")]'))
    )
    email_in = wait.until(
        EC.presence_of_element_located((By.XPATH, '//input[contains(@placeholder,"Email")]'))
    )

    pnr_in.clear()
    email_in.clear()
    pnr_in.send_keys(pnr)
    email_in.send_keys(email)

    wlog.info(f"[WORKER {wid}] {pnr}: form filled, clicking Retrieve")
    driver.find_element(By.XPATH, '//button[contains(.,"Retrieve")]').click()

    try:
        WebDriverWait(driver, WAIT_SHORT).until(
            EC.element_to_be_clickable((By.XPATH, "//span[contains(text(),'Get Your GST')]"))
        ).click()
        wlog.info(f"[WORKER {wid}] {pnr}: clicked 'Get Your GST'")
    except Exception:
        pass

    wlog.info(f"[WORKER {wid}] {pnr}: waiting for GST summary page")
    WebDriverWait(driver, WAIT).until(
        lambda d: "gst-invoice-summary" in d.current_url
    )

    WebDriverWait(driver, WAIT).until(
        EC.presence_of_element_located((By.XPATH, '//p[contains(.,"Invoice Number")]'))
    )

    elements = driver.find_elements(
        By.XPATH,
        '//p[.//span[contains(text(),"Invoice Number:")]]'
    )

    invoices = [
        e.text.split("Invoice Number:")[-1].strip()
        for e in elements
        if "Invoice Number:" in e.text
    ]

    wlog.info(f"[WORKER {wid}] {pnr}: found {len(invoices)} invoice(s)")
    return invoices


# ================= PDF =================

def download_pdf(driver, inv, wid="?"):

    path = os.path.join(INVOICE_DIR, f"{inv}.pdf")

    if os.path.exists(path) and os.path.getsize(path) > PDF_MIN_SIZE:
        return

    wlog = logging.getLogger(f"indigo.w{wid}")
    wlog.info(f"[WORKER {wid}] downloading invoice {inv}")

    # Always load images for the actual invoice page -- this is what gets
    # printed to PDF, so the IndiGo logo must be present regardless of
    # BLOCK_IMAGES being used for speed on the earlier search pages.
    set_image_blocking(driver, False)
    driver.get(f"https://www.goindigo.in/view-gst-invoice/print.html?invoiceNumber={inv}")
    wait_ready(driver)
    wait_images_loaded(driver)

    pdf = driver.execute_cdp_cmd("Page.printToPDF", {"printBackground": True})
    data = base64.b64decode(pdf["data"])

    if len(data) < PDF_MIN_SIZE:
        raise Exception("Blank PDF")

    with open(path, "wb") as f:
        f.write(data)


# ================= TASK (watched for hangs) =================

def process_pnr(driver, pnr, email, wid="?"):
    invoices = fetch_invoices(driver, pnr, email, wid)
    if not invoices:
        raise Exception("No invoices found")
    for inv in invoices:
        download_pdf(driver, inv, wid)
    return invoices


def run_with_timeout(driver, pnr, email, timeout, wid="?"):
    """
    Runs process_pnr in a helper daemon thread inside THIS worker process
    and waits up to `timeout` seconds. If it hangs, we reclaim control,
    recycle the driver, and move on -- so one bad PNR can never freeze
    a worker permanently.
    """
    result = {}

    def runner():
        try:
            result["invoices"] = process_pnr(driver, pnr, email, wid)
        except Exception as e:
            result["error"] = e

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    t.join(timeout)

    if t.is_alive():
        raise TimeoutError(f"Task exceeded {timeout}s (site likely stuck)")

    if "error" in result:
        raise result["error"]

    return result.get("invoices", [])


# ================= WORKER PROCESS =================

def worker(wid, task_queue, csv_lock):
    """
    Runs in its own OS process with its own independent Chrome browser.
    Pulls (pnr, email) tuples from the shared multiprocessing queue until
    it sees the sentinel None.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    wlog = logging.getLogger(f"indigo.w{wid}")
    wlog.info(f"[WORKER {wid}] starting own browser...")

    driver = create_driver(wid)
    wlog.info(f"[WORKER {wid}] browser ready.")
    processed_since_restart = 0

    while True:
        try:
            item = task_queue.get(timeout=5)
        except Empty:
            continue

        if item is None:
            break

        pnr, email = item
        attempt = 0
        succeeded = False
        last_error = None

        while attempt <= MAX_RETRIES and not succeeded:
            attempt += 1
            try:
                wlog.info(f"[WORKER {wid}] Processing {pnr} (attempt {attempt}/{MAX_RETRIES + 1})")
                invoices = run_with_timeout(driver, pnr, email, TASK_TIMEOUT, wid)
                write_output(csv_lock, pnr, invoices)
                succeeded = True
                processed_since_restart += 1

            except Exception as e:
                last_error = str(e)
                wlog.warning(f"[WORKER {wid}] {pnr} failed (attempt {attempt}): {last_error}")
                quit_driver(driver)
                driver = create_driver(wid)
                processed_since_restart = 0

        if not succeeded:
            write_error(csv_lock, pnr, last_error or "Unknown error")

        if processed_since_restart >= RESTART_AFTER:
            wlog.info(f"[WORKER {wid}] Recycling browser after {RESTART_AFTER} PNRs")
            quit_driver(driver)
            driver = create_driver(wid)
            processed_since_restart = 0

    quit_driver(driver)
    wlog.info(f"[WORKER {wid}] stopped.")


# ================= MAIN =================

def main():

    log.info("========== INDIGO INVOICE DOWNLOADER START (multi-browser) ==========")

    if RESUME and os.path.exists(OUTPUT_FILE) and os.path.exists(ERROR_FILE):
        done = load_done_pnrs()
        log.info(f"Resume mode: {len(done)} PNR(s) already processed, will be skipped.")
    else:
        init_files()
        done = set()

    with open(INPUT_FILE) as f:
        rows = list(csv.reader(f))[1:]

    pending = []
    for r in rows:
        if not r or not r[0].strip():
            continue
        pnr = r[0].strip()
        if pnr in done:
            continue
        email = r[1].strip() if len(r) > 1 else ""
        pending.append((pnr, email))

    log.info(f"Queued {len(pending)} PNR(s) for processing across {WORKERS} independent browser(s).")

    if not pending:
        log.info("Nothing to do.")
        return

    # 🔥 PRE-WARM: patch the undetected_chromedriver executable once here,
    # in the main process, before any worker processes exist. This is the
    # moment most likely to hit the one-time driver-patch race condition,
    # so doing it single-process first guarantees the cached exe is valid
    # before workers launch their own browsers in parallel.
    log.info("Pre-warming Chrome driver (one-time patch check)...")
    try:
        warm = create_driver("prewarm")
        quit_driver(warm)
        log.info("Driver pre-warm successful.")
    except Exception as e:
        log.error(f"Driver pre-warm failed: {e}")
        log.error(r"Try deleting %APPDATA%\undetected_chromedriver\ and rerunning.")
        return

    ctx = mp.get_context("spawn")   # 'spawn' is required/safest on Windows
    task_queue = ctx.Queue()
    csv_lock = ctx.Lock()

    for item in pending:
        task_queue.put(item)
    for _ in range(WORKERS):
        task_queue.put(None)  # one sentinel per worker to signal shutdown

    processes = []
    for wid in range(WORKERS):
        p = ctx.Process(target=worker, args=(wid, task_queue, csv_lock))
        p.start()
        processes.append(p)
        if wid < WORKERS - 1:
            time.sleep(STAGGER_START)

    for p in processes:
        p.join()

    log.info("========== INDIGO INVOICE DOWNLOADER END ==========")


if __name__ == "__main__":
    mp.freeze_support()   # harmless on non-frozen scripts, required if ever built into an .exe
    main()
