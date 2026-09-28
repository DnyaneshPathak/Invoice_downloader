"""
streamlit_app.py — GST Invoice Bulk Downloader (Streamlit UI)
=============================================================
Run locally with:
    streamlit run streamlit_app.py

WARNING: DO NOT deploy this app to any hosted/cloud service. LOCAL USE ONLY.
"""

from __future__ import annotations

import os
import platform
import queue as _queue
import subprocess
import threading
import time
from pathlib import Path

import pandas as pd
import streamlit as st

from automation_core import load_pnr_list, run_batch

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="GST Invoice Downloader",
    page_icon="🛫",
    layout="wide",
    initial_sidebar_state="collapsed",
)

DEFAULT_OUTPUT_DIR = r"C:\Users\Dnyanesh Pathak\OneDrive\Desktop\INVOICES"


def render_css() -> None:
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

    html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

    /* ── Premium Dark Theme (Linear/Vercel inspired) ── */
    .stApp {
        background-color: #000000;
        background-image: radial-gradient(at 0% 0%, rgba(37, 99, 235, 0.1) 0, transparent 50%), radial-gradient(at 100% 0%, rgba(124, 58, 237, 0.1) 0, transparent 50%);
        color: #e2e8f0;
    }

    .main-card {
        background: #0a0a0a;
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 12px;
        padding: 2rem 2.5rem;
        margin-bottom: 1.5rem;
        box-shadow: 0 10px 40px -10px rgba(0,0,0,0.5);
        transition: border-color 0.2s ease;
    }
    .main-card:hover { 
        border-color: rgba(255, 255, 255, 0.2); 
    }

    .hero-title {
        font-size: 2.4rem;
        font-weight: 800;
        background: linear-gradient(135deg, #e2e8f0 0%, #94a3b8 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin: 0 0 0.4rem 0;
        letter-spacing: -0.5px;
    }
    .hero-sub { color: #94a3b8; font-size: 1.1rem; margin: 0 0 1.5rem 0; font-weight: 400; }

    .local-warning {
        background: rgba(234, 179, 8, 0.05);
        border: 1px solid rgba(234, 179, 8, 0.2);
        border-radius: 8px;
        padding: .85rem 1.25rem;
        font-size: .9rem;
        color: #fde047;
        margin-bottom: 1rem;
        font-weight: 500;
    }

    /* ── Result row colours ── */
    .row-success { color: #4ade80; font-weight: 600; }
    .row-error   { color: #f87171; font-weight: 600; }
    .row-skip    { color: #94a3b8; }
    .row-invalid { color: #fbbf24; }

    /* ── Download complete banner ── */
    .done-banner {
        background: rgba(34, 197, 94, 0.05);
        border: 1px solid rgba(34, 197, 94, 0.2);
        border-radius: 12px;
        padding: 1.75rem 2rem;
        text-align: center;
        margin-top: 1rem;
    }
    .done-banner h2 { color: #4ade80; margin: 0 0 .5rem 0; font-size: 1.7rem; font-weight: 700; letter-spacing: -0.5px; }
    .done-banner p  { color: #86efac; margin: 0; font-size: 1.05rem; }

    .stButton > button {
        background: #ffffff !important;
        color: #000000 !important;
        border: none !important;
        border-radius: 8px !important;
        padding: .6rem 1.8rem !important;
        font-weight: 600 !important;
        font-size: 1rem !important;
        transition: all .2s ease !important;
        box-shadow: 0 4px 12px rgba(255, 255, 255, 0.1) !important;
    }
    .stButton > button:hover {
        transform: translateY(-2px) !important;
        background: #f8fafc !important;
        box-shadow: 0 8px 20px rgba(255, 255, 255, 0.15) !important;
    }

    [data-testid="stFileUploader"] {
        border: 1px dashed rgba(255, 255, 255, 0.2) !important;
        border-radius: 12px !important;
        padding: 1.5rem !important;
        background: rgba(255, 255, 255, 0.02) !important;
    }
    [data-testid="stFileUploader"]:hover {
        border-color: rgba(255, 255, 255, 0.4) !important;
    }

    .stSelectbox > div > div,
    .stTextInput > div > div > input {
        background: rgba(255, 255, 255, 0.03) !important;
        border: 1px solid rgba(255, 255, 255, 0.1) !important;
        border-radius: 8px !important;
        color: #f8fafc !important;
    }

    .stProgress > div > div > div > div {
        background: linear-gradient(90deg, #3b82f6, #8b5cf6) !important;
        border-radius: 6px !important;
    }

    [data-testid="stDataFrame"] { border-radius: 8px; overflow: hidden; border: 1px solid rgba(255,255,255,0.1); }
    hr { border-color: rgba(255,255,255,0.1) !important; }
    </style>
    """, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _auto_detect_columns(cols: list[str], airline: str) -> tuple[str, str]:
    pnr_guess = next(
        (c for c in cols if "pnr" in c.lower() or "booking" in c.lower() or "ref" in c.lower()),
        cols[0] if cols else "",
    )
    
    if airline == "IndiGo":
        secondary_guess = next(
            (c for c in cols if "email" in c.lower() or "mail" in c.lower()),
            cols[1] if len(cols) > 1 else cols[0] if cols else "",
        )
    else:
        # Air India Express
        secondary_guess = next(
            (c for c in cols if "origin" in c.lower() or "city" in c.lower()
             or "airport" in c.lower() or "code" in c.lower() or "station" in c.lower()),
            cols[1] if len(cols) > 1 else cols[0] if cols else "",
        )
        
    return pnr_guess, secondary_guess


def _open_folder(path: str) -> None:
    try:
        if platform.system() == "Windows":
            os.startfile(path)
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as e:
        st.error(f"Could not open folder: {e}")


def _status_icon(s: str) -> str:
    return {"SUCCESS": "✅", "ERROR": "❌", "SKIPPED": "⏭️",
            "INVALID": "⚠️", "CAPTCHA_TIMEOUT": "🔒",
            "UNAVAILABLE": "🔄"}.get(s, "ℹ️")


# ---------------------------------------------------------------------------
# Main Application
# ---------------------------------------------------------------------------

def init_session_state() -> None:
    defaults = {
        "df_raw": None,
        "df_clean": None,
        "results": [],
        "running": False,
        "done": False,
        "output_dir": DEFAULT_OUTPUT_DIR,
        "n_ok": 0,
        "n_err": 0,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

def reset_session_state() -> None:
    defaults = {
        "results": [],
        "running": False,
        "done": False,
        "n_ok": 0,
        "n_err": 0,
    }
    for k, v in defaults.items():
        st.session_state[k] = v


def render_header() -> None:
    st.markdown("""
    <div class="main-card">
      <p class="hero-title">🛫 GST Invoice Downloader</p>
      <p class="hero-sub">Bulk-download GST invoice PDFs for multiple airlines — 100% local, no cloud, no third-party APIs.</p>
      <div class="local-warning">
        ⚠️  <strong>LOCAL USE ONLY</strong> — Run with
        <code>streamlit run streamlit_app.py</code> on your own machine.
        Do NOT deploy to Streamlit Cloud or any hosted service.
      </div>
    </div>
    """, unsafe_allow_html=True)


def main() -> None:
    render_css()
    init_session_state()
    render_header()

    # ── Step 1: Upload ──
    st.markdown('<div class="main-card">', unsafe_allow_html=True)
    st.subheader("📂 Step 1 — Select Airline & Upload Excel")

    airline = st.selectbox(
        "Select the Airline Platform:",
        options=["Air India Express", "IndiGo"],
        key="airline_selector"
    )

    if airline == "Air India Express":
        st.caption("File must contain a **PNR** column and an **Origin City** column (city name as shown on your ticket, e.g. Mumbai, Delhi, Bangalore).")
    else:
        st.caption("File must contain a **PNR** column and an **Email** column.")

    uploaded = st.file_uploader(
        "Drag & drop your .xlsx file here, or click to browse",
        type=["xlsx"],
        key="excel_upload",
    )
    if uploaded:
        try:
            df_raw = pd.read_excel(uploaded, dtype=str)
            st.session_state["df_raw"] = df_raw
            st.success(f"✅  Loaded **{len(df_raw)} rows** × {len(df_raw.columns)} columns.")
        except Exception as e:
            st.error(f"Failed to read Excel file: {e}")
            st.session_state["df_raw"] = None
    st.markdown('</div>', unsafe_allow_html=True)

    # ── Steps 2-4: Process ──
    if st.session_state["df_raw"] is not None:
        df_raw: pd.DataFrame = st.session_state["df_raw"]
        cols = list(df_raw.columns)
        pnr_guess, secondary_guess = _auto_detect_columns(cols, airline)

        # ── Step 2: Mapping ──
        st.markdown('<div class="main-card">', unsafe_allow_html=True)
        st.subheader("👁️ Step 2 — Preview & Column Mapping")
        st.dataframe(df_raw.head(5), width="stretch", hide_index=True)

        def format_dropdown_option(col_name):
            if col_name == pnr_guess:
                return "PNR/Booking Reference Number"
            elif col_name == secondary_guess:
                return "Origin City Name / Email id"
            return col_name

        col_a, col_b = st.columns(2)
        with col_a:
            pnr_col = st.selectbox(
                "PNR/Booking Reference Number",
                options=cols,
                index=cols.index(pnr_guess) if pnr_guess in cols else 0,
                format_func=format_dropdown_option,
                key="pnr_col",
            )
        with col_b:
            secondary_label = "Origin City Name / Email id"
            secondary_col = st.selectbox(
                secondary_label,
                options=cols,
                index=cols.index(secondary_guess) if secondary_guess in cols else min(1, len(cols) - 1),
                format_func=format_dropdown_option,
                key="secondary_col",
            )
        st.markdown('</div>', unsafe_allow_html=True)

        # ── Step 3: Settings ──
        st.markdown('<div class="main-card">', unsafe_allow_html=True)
        st.subheader("⚙️ Step 3 — Settings")

        s1, s2, s3 = st.columns([2, 1, 1])
        with s1:
            output_dir = st.text_input(
                "Output root folder (each PNR gets its own sub-folder)",
                value=st.session_state["output_dir"],
                key="output_dir_input",
            )
            st.session_state["output_dir"] = output_dir
            st.caption(f"📁  Save path: `{output_dir}\\{{PNR}}\\invoice.pdf`")
        with s2:
            min_delay = st.number_input("Min delay (s)", 1.0, 30.0, 4.0, 0.5, key="min_delay")
            max_delay = st.number_input("Max delay (s)", 1.0, 60.0, 9.0, 0.5, key="max_delay")
        with s3:
            headless = st.checkbox(
                "Headless (hide browser)",
                value=False, key="headless",
                help="Keep OFF — you need to see the browser to solve CAPTCHAs.",
            )
            test_mode = st.checkbox(
                "🧪 Test mode (1st row only)",
                value=False, key="test_mode",
                help="Run only the first PNR to verify before committing to the full batch.",
            )
        if headless:
            st.warning("⚠️  Headless ON — CAPTCHAs cannot be solved. Keep OFF for normal use.")

        st.markdown('</div>', unsafe_allow_html=True)

        # ── Step 4: Run ──
        st.markdown('<div class="main-card">', unsafe_allow_html=True)
        st.subheader("🚀 Step 4 — Run")

        df_clean = None
        limit = None
        n_to_run = 0
        if not st.session_state["running"] and not st.session_state["done"]:
            try:
                uploaded.seek(0)
                df_clean = load_pnr_list(uploaded, pnr_col, secondary_col)
                st.session_state["df_clean"] = df_clean
                limit = 1 if test_mode else None
                n_to_run = 1 if test_mode else len(df_clean)
                st.info(f"Ready to process **{n_to_run}** PNR(s) for **{airline}**.  "
                        f"Invoices → `{output_dir}\\{{PNR}}\\invoice.pdf`")
            except Exception as e:
                st.error(f"Could not prepare data: {e}")
        else:
            df_clean = st.session_state.get("df_clean")
            if df_clean is not None:
                limit = 1 if st.session_state.get("test_mode", False) else None
                n_to_run = 1 if limit else len(df_clean)

        start_clicked = st.button(
            "▶  Start Download",
            disabled=(st.session_state["running"] or df_clean is None),
            key="start_btn",
        )

        if start_clicked and df_clean is not None and not st.session_state["running"]:
            reset_session_state()
            st.session_state["running"] = True
            st.rerun()

        # ── Live progress ──
        if st.session_state["running"] or st.session_state["done"]:

            prog_bar   = st.progress(0, text="Starting…")
            status_txt = st.empty()
            results_ph = st.empty()

            def _render_table(data: list) -> None:
                if not data:
                    return
                df_disp = pd.DataFrame(
                    [(f"{_status_icon(s)} {s}", p, o, d) for p, o, s, d in data],
                    columns=["Status", "PNR", "Secondary Input", "Detail"],
                )
                results_ph.dataframe(df_disp, width="stretch", hide_index=True)

            if st.session_state["running"] and not st.session_state["done"]:
                _q: _queue.Queue = _queue.Queue()
                _exc_holder: list[Exception] = []

                def _progress(idx: int, total: int, pnr: str, secondary: str,
                              status: str, detail: str) -> None:
                    _q.put((idx, total, pnr, secondary, status, detail))

                def _run_thread() -> None:
                    try:
                        run_batch(
                            df=df_clean,
                            airline=airline,
                            pnr_col=pnr_col,
                            secondary_col=secondary_col,
                            output_dir=output_dir,
                            headless=headless,
                            min_delay=min_delay,
                            max_delay=max_delay,
                            limit=limit,
                            progress_callback=_progress,
                        )
                    except Exception as exc:
                        _exc_holder.append(exc)
                    finally:
                        st.session_state["running"] = False
                        st.session_state["done"] = True

                t = threading.Thread(target=_run_thread, daemon=True)
                t.start()

                last_idx = -1
                while t.is_alive() or not _q.empty():
                    time.sleep(0.5)
                    while not _q.empty():
                        idx, total, pnr, secondary, status, detail = _q.get()
                        st.session_state["results"].append((pnr, secondary, status, detail))
                        if status == "SUCCESS":
                            st.session_state["n_ok"] += 1
                        elif status not in ("SKIPPED",):
                            st.session_state["n_err"] += 1
                        last_idx = idx
                        prog = (idx + 1) / max(total, 1)
                        icon = _status_icon(status)
                        prog_bar.progress(
                            prog,
                            text=f"{icon} [{idx+1}/{total}]  PNR **{pnr}** ({secondary}) — {status}",
                        )
                        status_txt.empty()
                    _render_table(st.session_state["results"])
                    if not t.is_alive() and _q.empty():
                        break

                t.join(timeout=5)

                if _exc_holder:
                    st.error(f"Batch error: {_exc_holder[0]}")

                st.session_state["running"] = False
                st.session_state["done"] = True
                st.rerun()

            # ── DONE state ──
            if st.session_state["done"]:
                results = st.session_state["results"]
                prog_bar.progress(1.0, text="✅  Batch complete!")
                _render_table(results)

                n_ok  = st.session_state["n_ok"]
                n_err = st.session_state["n_err"]
                total = len(results)

                st.markdown(f"""
                <div class="done-banner">
                  <h2>⬇️ Download Complete</h2>
                  <p>
                    <strong>{n_ok}</strong> invoice(s) downloaded successfully
                    &nbsp;·&nbsp;
                    <strong>{n_err}</strong> error(s)
                    &nbsp;·&nbsp;
                    <strong>{total}</strong> total processed
                  </p>
                  <p style="margin-top:.6rem;font-size:.9rem;color:#9ae6b4;">
                    📁 Saved to: <code>{output_dir}</code>
                  </p>
                </div>
                """, unsafe_allow_html=True)

                c1, c2, _ = st.columns([1, 1, 3])
                with c1:
                    if st.button("📂 Open Output Folder", key="open_folder_btn"):
                        _open_folder(output_dir)
                with c2:
                    if st.button("🔄 Run Again / New File", key="reset_btn"):
                        reset_session_state()
                        st.rerun()

        st.markdown('</div>', unsafe_allow_html=True)


if __name__ == "__main__":
    main()
