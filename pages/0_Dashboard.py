import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import calendar
import streamlit as st
import pandas as pd
from datetime import datetime, timedelta

from data.yf_data import resolve_ticker, fetch_last_price, fetch_option_expirations, fetch_option_chain
from data.db_manager import (
    init_db,
    get_watchlist, add_watchlist, remove_watchlist,
    get_portfolio, add_portfolio, remove_portfolio,
    upsert_options_summary, get_options_summary,
)

# ── Init DB ───────────────────────────────────────────────────────────────────
init_db()

# ── Page config ───────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .block-container { padding-top: 1rem; }
    div[data-testid="stMetricValue"] { font-size: 1.3rem; font-weight: 700; }
    h2, h3 { color: #e2e8f0; }
    .wl-ticker  { font-size: 1.05rem; font-weight: 700; color: #38bdf8; }
    .wl-price   { font-size: 1.05rem; font-weight: 600; color: #4ade80; }
    .pf-pnl-pos { color: #4ade80; font-weight: 600; }
    .pf-pnl-neg { color: #f87171; font-weight: 600; }
</style>
""", unsafe_allow_html=True)

st.title("🏠 Dashboard")

# ── Helper functions ──────────────────────────────────────────────────────────

def _third_friday(year: int, month: int) -> str | None:
    count = 0
    for day in range(1, calendar.monthrange(year, month)[1] + 1):
        if datetime(year, month, day).weekday() == 4:
            count += 1
            if count == 3:
                return f"{year}-{month:02d}-{day:02d}"
    return None


def _month_end(year: int, month: int) -> str:
    last_day = calendar.monthrange(year, month)[1]
    return f"{year}-{month:02d}-{last_day:02d}"


def _select_expiries(raw_expiries: list[str]) -> list[str]:
    """
    From a list of available expiry strings, return:
    - All expiries within the next 15 days
    - The best available expiry for the next 3 calendar months
      (prefer 3rd Friday; fall back to nearest available date in that month)
    """
    today = datetime.today().date()
    cutoff_15 = today + timedelta(days=15)
    exp_set = set(raw_expiries)

    selected = set()

    # --- Next 15 days ---
    for e in raw_expiries:
        try:
            d = datetime.strptime(e, "%Y-%m-%d").date()
            if today <= d <= cutoff_15:
                selected.add(e)
        except ValueError:
            pass

    # --- Next 3 monthly expiries ---
    year, month = today.year, today.month
    found = 0
    for _ in range(24):          # scan up to 24 months ahead
        month += 1
        if month > 12:
            month = 1
            year += 1
        if found >= 3:
            break

        candidates = []
        tf = _third_friday(year, month)
        me = _month_end(year, month)

        # 3rd Friday first
        if tf and tf in exp_set:
            candidates.append(tf)
        # Month-end
        if me in exp_set:
            candidates.append(me)

        # Fallback: nearest available date in this month
        if not candidates:
            month_dates = [
                e for e in raw_expiries
                if e.startswith(f"{year}-{month:02d}-")
            ]
            if month_dates:
                candidates.append(sorted(month_dates)[-1])   # latest in month

        if candidates:
            selected.add(candidates[0])
            found += 1

    return sorted(selected)


def _atm_iv(df: pd.DataFrame, spot: float) -> float:
    if df.empty or spot is None:
        return float("nan")
    clean = df[df["impliedVolatility"] > 0].copy()
    if clean.empty:
        return float("nan")
    idx = (clean["strike"] - spot).abs().idxmin()
    return round(clean.loc[idx, "impliedVolatility"] * 100, 2)


def _navigate_to(page_path: str, ticker: str):
    st.session_state["selected_ticker"] = ticker
    st.switch_page(page_path)


# ══════════════════════════════════════════════════════════════════════════════
# Layout: two columns
# ══════════════════════════════════════════════════════════════════════════════
col_wl, col_pf = st.columns([1.2, 1], gap="large")

# ─────────────────────────────────────────────────────────────────────────────
# LEFT COLUMN — Watchlist
# ─────────────────────────────────────────────────────────────────────────────
with col_wl:
    st.subheader("📋 Watchlist")

    wl_items = get_watchlist()

    if not wl_items:
        st.info("Watchlist is empty. Add a ticker below.")
    else:
        # Table header
        hdr = st.columns([1.4, 1.2, 0.9, 0.9, 0.5])
        hdr[0].markdown("**Ticker**")
        hdr[1].markdown("**Name**")
        hdr[2].markdown("**Price**")
        hdr[3].markdown("**Chart**")
        hdr[4].markdown("**Opts**")
        st.divider()

        for item in wl_items:
            raw_ticker = item["ticker"]
            yf_ticker  = resolve_ticker(raw_ticker)
            price      = fetch_last_price(yf_ticker)
            price_str  = f"${price:,.2f}" if price else "—"

            row = st.columns([1.4, 1.2, 0.9, 0.9, 0.5])
            row[0].markdown(f"<span class='wl-ticker'>{raw_ticker}</span>", unsafe_allow_html=True)
            row[1].write(item["display_name"] or "—")
            row[2].markdown(f"<span class='wl-price'>{price_str}</span>", unsafe_allow_html=True)

            if row[3].button("📈 Chart", key=f"chart_{raw_ticker}"):
                _navigate_to("pages/1_Chart.py", raw_ticker)

            if row[4].button("🎯", key=f"opts_{raw_ticker}", help="Options Chain"):
                _navigate_to("pages/2_Options.py", raw_ticker)

    st.divider()

    # Add / Remove controls
    add_col, rem_col = st.columns(2)

    with add_col:
        st.markdown("**Add Ticker**")
        new_ticker = st.text_input("Ticker", key="wl_add_ticker", label_visibility="collapsed",
                                   placeholder="e.g. NVDA").upper().strip()
        new_name   = st.text_input("Name (optional)", key="wl_add_name", label_visibility="collapsed",
                                   placeholder="Display name")
        if st.button("➕ Add to Watchlist", use_container_width=True):
            if new_ticker:
                add_watchlist(new_ticker, new_name)
                st.success(f"{new_ticker} added.")
                st.rerun()
            else:
                st.warning("Enter a ticker symbol.")

    with rem_col:
        st.markdown("**Remove Ticker**")
        tickers_in_wl = [i["ticker"] for i in wl_items]
        if tickers_in_wl:
            to_remove = st.selectbox("Select", tickers_in_wl, key="wl_remove_sel",
                                     label_visibility="collapsed")
            if st.button("🗑️ Remove", use_container_width=True):
                remove_watchlist(to_remove)
                st.success(f"{to_remove} removed.")
                st.rerun()
        else:
            st.caption("Nothing to remove.")

    # Refresh button
    st.divider()
    if st.button("🔄 Refresh Prices", use_container_width=True):
        st.cache_data.clear()
        st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# RIGHT COLUMN — Portfolio
# ─────────────────────────────────────────────────────────────────────────────
with col_pf:
    st.subheader("💼 Portfolio")

    pf_items = get_portfolio()

    if not pf_items:
        st.info("No positions yet. Add one below.")
    else:
        for pos in pf_items:
            raw_ticker  = pos["ticker"]
            yf_ticker   = resolve_ticker(raw_ticker)
            cur_price   = fetch_last_price(yf_ticker)
            entry       = pos["entry_price"]
            qty         = pos["quantity"]
            pnl         = (cur_price - entry) * qty if cur_price else None
            pnl_pct     = ((cur_price / entry) - 1) * 100 if cur_price and entry else None

            with st.container():
                c1, c2, c3 = st.columns([1.2, 2.5, 0.6])
                c1.markdown(f"**{raw_ticker}**")

                details = (
                    f"Qty: {qty:g} | Entry: ${entry:,.2f} | "
                    f"Date: {pos['entry_date']}"
                )
                if cur_price:
                    pnl_color  = "pf-pnl-pos" if pnl >= 0 else "pf-pnl-neg"
                    pnl_sign   = "+" if pnl >= 0 else ""
                    details += (
                        f" | Now: ${cur_price:,.2f} | "
                        f"<span class='{pnl_color}'>{pnl_sign}${pnl:,.2f} "
                        f"({pnl_sign}{pnl_pct:.1f}%)</span>"
                    )
                c2.markdown(details, unsafe_allow_html=True)

                if c3.button("🗑️", key=f"del_pf_{pos['id']}", help="Remove position"):
                    remove_portfolio(pos["id"])
                    st.rerun()

        st.divider()

    # Add position form
    with st.expander("➕ Add Position", expanded=not bool(pf_items)):
        with st.form("add_position_form", clear_on_submit=True):
            f1, f2 = st.columns(2)
            pf_ticker = f1.text_input("Ticker", placeholder="e.g. AAPL").upper().strip()
            pf_qty    = f2.number_input("Quantity", min_value=0.0001, value=1.0, step=1.0)
            f3, f4 = st.columns(2)
            pf_price  = f3.number_input("Entry Price ($)", min_value=0.0001, value=100.0, step=0.01)
            pf_date   = f4.date_input("Entry Date", value=datetime.today())
            submitted = st.form_submit_button("Add Position", use_container_width=True)
            if submitted:
                if pf_ticker:
                    add_portfolio(pf_ticker, pf_qty, pf_price, str(pf_date))
                    st.success(f"Position added: {pf_ticker}")
                    st.rerun()
                else:
                    st.warning("Enter a ticker symbol.")


# ══════════════════════════════════════════════════════════════════════════════
# BOTTOM SECTION — Options Snapshot
# ══════════════════════════════════════════════════════════════════════════════
st.divider()
st.subheader("📊 Options Snapshot")

opt_btn_col, opt_info_col = st.columns([0.35, 0.65])

with opt_btn_col:
    fetch_all = st.button("⚡ Fetch Options Data for Watchlist", use_container_width=True,
                          help="Fetches next 15 days + 3 monthly expiries for all watchlist tickers")

if fetch_all:
    wl_items_fresh = get_watchlist()
    tickers_to_fetch = [i["ticker"] for i in wl_items_fresh]
    total  = len(tickers_to_fetch)
    errors = []

    progress_bar = st.progress(0, text="Starting…")
    status_area  = st.empty()

    for idx, raw_ticker in enumerate(tickers_to_fetch):
        yf_ticker = resolve_ticker(raw_ticker)
        progress_bar.progress((idx) / total, text=f"Fetching {raw_ticker} ({idx+1}/{total})…")
        status_area.caption(f"Processing **{raw_ticker}** → `{yf_ticker}`")

        try:
            expirations = fetch_option_expirations(yf_ticker)
            if not expirations:
                errors.append(f"{raw_ticker}: no expirations found")
                continue

            spot = fetch_last_price(yf_ticker)
            selected = _select_expiries(expirations)

            for expiry in selected:
                try:
                    calls, puts = fetch_option_chain(yf_ticker, expiry)
                    iv_call  = _atm_iv(calls, spot) if spot else None
                    iv_put   = _atm_iv(puts,  spot) if spot else None
                    c_oi     = int(calls["openInterest"].sum()) if "openInterest" in calls.columns else None
                    p_oi     = int(puts["openInterest"].sum())  if "openInterest" in puts.columns  else None
                    pc       = round(p_oi / c_oi, 3) if (c_oi and p_oi and c_oi > 0) else None
                    upsert_options_summary(raw_ticker, expiry, iv_call, iv_put, c_oi, p_oi, pc)
                except Exception as e:
                    errors.append(f"{raw_ticker} {expiry}: {e}")

        except Exception as e:
            errors.append(f"{raw_ticker}: {e}")

    progress_bar.progress(1.0, text="Done.")
    status_area.empty()

    if errors:
        with st.expander(f"⚠️ {len(errors)} error(s) during fetch", expanded=False):
            for err in errors:
                st.caption(err)
    else:
        st.success("Options data fetched and saved successfully.")

# ── Display saved options summary ─────────────────────────────────────────────
saved_rows = get_options_summary()
if saved_rows:
    df_opts = pd.DataFrame(saved_rows)
    df_opts = df_opts.rename(columns={
        "ticker":        "Ticker",
        "expiry_date":   "Expiry",
        "atm_iv_call":   "ATM IV Call %",
        "atm_iv_put":    "ATM IV Put %",
        "total_call_oi": "Call OI",
        "total_put_oi":  "Put OI",
        "pc_ratio":      "P/C Ratio",
        "fetched_at":    "Fetched (UTC)",
    })
    # Trim timestamp to minute for display
    df_opts["Fetched (UTC)"] = df_opts["Fetched (UTC)"].str[:16]

    # Show as grouped tabs by ticker
    tickers_with_data = df_opts["Ticker"].unique().tolist()
    if len(tickers_with_data) <= 10:
        tabs = st.tabs(tickers_with_data)
        for tab, tkr in zip(tabs, tickers_with_data):
            with tab:
                subset = df_opts[df_opts["Ticker"] == tkr].drop(columns=["id", "Ticker"], errors="ignore")
                st.dataframe(subset, use_container_width=True, hide_index=True)
    else:
        st.dataframe(
            df_opts.drop(columns=["id"], errors="ignore"),
            use_container_width=True,
            hide_index=True,
        )
else:
    st.caption("No options data saved yet. Click the Fetch button above.")
