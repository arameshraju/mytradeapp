import sys
from pathlib import Path

# Ensure the project root is on sys.path so `data.yf_data` resolves correctly
# regardless of how Streamlit loads this page file.
sys.path.insert(0, str(Path(__file__).parent.parent))

import bisect
import streamlit as st
import pandas as pd
import numpy as np
import calendar
from datetime import datetime, timedelta
import plotly.graph_objects as go

from data.yf_data import resolve_ticker, fetch_option_expirations, fetch_option_chain, fetch_last_price


# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .block-container { padding-top: 1rem; }
    div[data-testid="stMetricValue"] { font-size: 1.4rem; font-weight: 700; }
    div[data-testid="stMetricDelta"] { font-size: 0.85rem; }
    h2, h3 { color: #e2e8f0; }
</style>
""", unsafe_allow_html=True)

# ── Helpers ───────────────────────────────────────────────────────────────────
def _dte(date_str: str) -> int:
    """Return days-to-expiry for a 'YYYY-MM-DD' string."""
    try:
        return max(0, (datetime.strptime(date_str, "%Y-%m-%d").date() - datetime.today().date()).days)
    except ValueError:
        return 0


def _filter_strikes(df: pd.DataFrame, spot: float, n: int) -> pd.DataFrame:
    """Keep n strikes on each side of spot."""
    if df.empty or spot is None:
        return df
    strikes = sorted(df["strike"].unique())
    idx = bisect.bisect_left(strikes, spot)
    lo  = max(0, idx - n)
    hi  = min(len(strikes), idx + n + 1)
    return df[df["strike"].isin(strikes[lo:hi])].copy()


def _atm_iv(df: pd.DataFrame, spot: float) -> float:
    """Return the implied volatility (%) of the strike nearest to spot."""
    if df.empty or spot is None:
        return float("nan")
    df_clean = df[df["impliedVolatility"] > 0].copy()
    if df_clean.empty:
        return float("nan")
    idx = (df_clean["strike"] - spot).abs().idxmin()
    return round(df_clean.loc[idx, "impliedVolatility"] * 100, 2)


def _third_friday(year: int, month: int):
    """Return 'YYYY-MM-DD' of the 3rd Friday of the given year/month, or None."""
    count = 0
    for day in range(1, calendar.monthrange(year, month)[1] + 1):
        if datetime(year, month, day).weekday() == 4:   # 4 = Friday
            count += 1
            if count == 3:
                return f"{year}-{month:02d}-{day:02d}"
    return None


def _best_monthly(year: int, month: int, exp_set: set):
    """
    Best monthly expiry for a given year/month from available expirations.
    Preference: 3rd Friday → scan back up to 7 days → latest date in month.
    """
    target = _third_friday(year, month)
    if target and target in exp_set:
        return target
    if target:
        d = datetime.strptime(target, "%Y-%m-%d")
        for _ in range(7):
            d -= timedelta(days=1)
            s = d.strftime("%Y-%m-%d")
            if s in exp_set:
                return s
    last_day = calendar.monthrange(year, month)[1]
    for day in range(last_day, 0, -1):
        s = f"{year}-{month:02d}-{day:02d}"
        if s in exp_set:
            return s
    return None


def _aggregate_chains(ticker: str, expiry_list: list) -> tuple:
    """Fetch chains for multiple expiries; sum OI per strike, average IV."""
    all_calls, all_puts = [], []
    for exp in expiry_list:
        c, p = fetch_option_chain(ticker, exp)
        if not c.empty:
            all_calls.append(c[["strike", "openInterest", "impliedVolatility"]].copy())
        if not p.empty:
            all_puts.append(p[["strike", "openInterest", "impliedVolatility"]].copy())

    def _agg(frames):
        if not frames:
            return pd.DataFrame(columns=["strike", "openInterest", "impliedVolatility"])
        combined = pd.concat(frames, ignore_index=True)
        combined["openInterest"] = combined["openInterest"].fillna(0)
        return (
            combined.groupby("strike", as_index=False)
            .agg(
                openInterest=("openInterest", "sum"),
                impliedVolatility=("impliedVolatility", "mean"),
            )
        )

    return _agg(all_calls), _agg(all_puts)


def _sr_levels(calls_oi: pd.DataFrame, puts_oi: pd.DataFrame, spot, n: int = 2) -> tuple:
    """
    Top-N call OI strikes >= spot  -> Resistance.
    Top-N put  OI strikes <= spot  -> Support.
    Returns (resistances, supports), each a list of (strike, oi) tuples.
    """
    if spot is None or calls_oi.empty or puts_oi.empty:
        return [], []
    c = calls_oi[calls_oi["openInterest"].fillna(0) > 0].copy()
    p = puts_oi[puts_oi["openInterest"].fillna(0)   > 0].copy()
    r_df = c[c["strike"] >= spot].nlargest(n, "openInterest") if not c.empty else pd.DataFrame()
    s_df = p[p["strike"] <= spot].nlargest(n, "openInterest") if not p.empty else pd.DataFrame()
    resistances = [(float(row.strike), int(row.openInterest)) for row in r_df.itertuples()]
    supports    = [(float(row.strike), int(row.openInterest)) for row in s_df.itertuples()]
    return resistances, supports


def _add_sr_vlines(fig, resistances: list, supports: list) -> None:
    """Overlay S/R dotted vlines with labels on a Plotly figure (strike on x-axis)."""
    for i, (strike, oi) in enumerate(resistances[:2]):
        fig.add_vline(
            x=strike, line_dash="dot", line_color="#ef5350",
            annotation_text=f"R{i + 1}  {strike:.0f}",
            annotation_position="top left",
            annotation_font_color="#ef5350",
        )
    for i, (strike, oi) in enumerate(supports[:2]):
        fig.add_vline(
            x=strike, line_dash="dot", line_color="#26a69a",
            annotation_text=f"S{i + 1}  {strike:.0f}",
            annotation_position="top right",
            annotation_font_color="#26a69a",
        )


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("⚙️ Options Settings")
    st.divider()
    _default_ticker = st.session_state.pop("selected_ticker", "SPY")
    ticker_raw = st.text_input(
        "Underlying Symbol", value=_default_ticker,
        help="E.g. SPY, AAPL, TSLA, QQQ, NVDA · SPX→^GSPC, NDX→^NDX, VIX→^VIX"
    ).upper()
    ticker = resolve_ticker(ticker_raw)
    st.divider()
    n_strikes = st.slider(
        "Strikes around ATM", min_value=5, max_value=50, value=20,
        help="Number of strikes to display on each side of the current price"
    )
    show_table = st.checkbox("Show raw chain table", value=False)

# ── Title ─────────────────────────────────────────────────────────────────────
display_ticker = f"{ticker_raw} ({ticker})" if ticker_raw != ticker else ticker
st.title(f"🎯  {display_ticker}  —  Options Chain")

# ── Fetch expirations ─────────────────────────────────────────────────────────
with st.spinner("Loading expiration dates…"):
    expirations = fetch_option_expirations(ticker)

if not expirations:
    st.error(
        f"⚠️ No options data found for **{ticker}**. "
        "Try liquid tickers like SPY, AAPL, TSLA, QQQ, NVDA."
    )
    st.stop()

mode = st.radio(
    "Expiry view",
    options=["📅 Next 15 Days", "📆 3 Monthlies", "🗓️ User Select"],
    horizontal=True,
    label_visibility="collapsed",
)

today   = datetime.today().date()
exp_set = set(expirations)

if mode == "📅 Next 15 Days":
    cutoff = today + timedelta(days=15)
    selected_expiries = [
        e for e in expirations
        if datetime.strptime(e, "%Y-%m-%d").date() <= cutoff
    ]
    if not selected_expiries:
        selected_expiries = expirations[: min(3, len(expirations))]
    n_exp = len(selected_expiries)
    expiry_word = "expiry" if n_exp == 1 else "expiries"
    mode_label = (
        f"Next 15 Days — {n_exp} {expiry_word} aggregated"
        f"  ({', '.join(selected_expiries)})"
    )

elif mode == "📆 3 Monthlies":
    selected_expiries = []
    for i in range(1, 4):
        yr  = today.year  + (today.month - 1 + i) // 12
        mo  = (today.month - 1 + i) % 12 + 1
        best = _best_monthly(yr, mo, exp_set)
        if best and best not in selected_expiries:
            selected_expiries.append(best)
    if not selected_expiries:
        selected_expiries = expirations[: min(3, len(expirations))]
    mode_label = f"3 Monthlies — {', '.join(selected_expiries)}"

else:  # 🗓️ User Select
    expiry = st.selectbox(
        "📅 Expiration Date",
        options=expirations,
        format_func=lambda d: f"{d}  ({_dte(d)} DTE)",
    )
    selected_expiries = [expiry]
    mode_label = f"{expiry}  ({_dte(expiry)} DTE)"

st.caption(f"📅 {mode_label}")
st.divider()

# ── Fetch chain + spot ────────────────────────────────────────────────────────
is_aggregated = len(selected_expiries) > 1
_spinner_msg  = (
    f"Aggregating {len(selected_expiries)} option chains…"
    if is_aggregated
    else f"Loading option chain for {selected_expiries[0]}…"
)

with st.spinner(_spinner_msg):
    if is_aggregated:
        calls, puts = _aggregate_chains(ticker, selected_expiries)
    else:
        calls, puts = fetch_option_chain(ticker, selected_expiries[0])
    spot = fetch_last_price(ticker)

# If spot unavailable, fall back to midpoint of available strikes
if spot is None and not calls.empty:
    all_strikes = sorted(calls["strike"].unique())
    spot = float(all_strikes[len(all_strikes) // 2])

# ── Filter strikes around ATM ─────────────────────────────────────────────────
calls_f = _filter_strikes(calls, spot, n_strikes)
puts_f  = _filter_strikes(puts,  spot, n_strikes)

# Subsets for IV chart — exclude zero / missing IV
calls_iv = calls_f[calls_f["impliedVolatility"] > 0].sort_values("strike")
puts_iv  = puts_f[puts_f["impliedVolatility"]  > 0].sort_values("strike")

resistances, supports = _sr_levels(calls_f, puts_f, spot)

# ── KPI metrics ───────────────────────────────────────────────────────────────
total_call_oi = int(calls["openInterest"].fillna(0).sum())
total_put_oi  = int(puts["openInterest"].fillna(0).sum())
pc_ratio      = round(total_put_oi / total_call_oi, 2) if total_call_oi else float("inf")
atm_call_iv   = _atm_iv(calls_f, spot)
atm_put_iv    = _atm_iv(puts_f,  spot)

m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("Spot Price",    f"{spot:.2f}" if spot else "N/A")
m2.metric("ATM Call IV",   f"{atm_call_iv:.1f}%" if not np.isnan(atm_call_iv) else "N/A")
m3.metric("ATM Put IV",    f"{atm_put_iv:.1f}%"  if not np.isnan(atm_put_iv)  else "N/A")
m4.metric("P/C OI Ratio",  f"{pc_ratio:.2f}" if pc_ratio != float("inf") else "∞")
m5.metric("Total Call OI", f"{total_call_oi:,}")
m6.metric("Total Put OI",  f"{total_put_oi:,}")

if resistances or supports:
    st.divider()
    sr_cols = st.columns(4)
    for i, (strike, oi) in enumerate(resistances[:2]):
        sr_cols[i].metric(
            f"🔴 Resistance {i + 1}",
            f"{strike:.2f}",
            f"Call OI  {oi:,}",
            delta_color="inverse",
        )
    for i, (strike, oi) in enumerate(supports[:2]):
        sr_cols[2 + i].metric(
            f"🟢 Support {i + 1}",
            f"{strike:.2f}",
            f"Put OI  {oi:,}",
        )

st.divider()

# ── Charts ────────────────────────────────────────────────────────────────────
DARK_BG  = "#0f1117"
GRID_CLR = "#1e222d"
LAYOUT_BASE = dict(
    template="plotly_dark",
    paper_bgcolor=DARK_BG,
    plot_bgcolor=DARK_BG,
    height=420,
    margin=dict(l=55, r=25, t=45, b=45),
    legend=dict(orientation="h", y=1.06, x=0, bgcolor="rgba(0,0,0,0)"),
)

chart_left, chart_right = st.columns(2)

# ── Left: IV Smile ────────────────────────────────────────────────────────────
with chart_left:
    fig_iv = go.Figure()

    fig_iv.add_trace(go.Scatter(
        x=calls_iv["strike"],
        y=(calls_iv["impliedVolatility"] * 100).round(2),
        name="Calls IV",
        mode="lines+markers",
        line=dict(color="#38bdf8", width=2),
        marker=dict(size=5),
    ))
    fig_iv.add_trace(go.Scatter(
        x=puts_iv["strike"],
        y=(puts_iv["impliedVolatility"] * 100).round(2),
        name="Puts IV",
        mode="lines+markers",
        line=dict(color="#fb923c", width=2),
        marker=dict(size=5),
    ))
    if spot:
        fig_iv.add_vline(
            x=spot, line_dash="dash", line_color="rgba(255,255,255,0.4)",
            annotation_text=f"ATM {spot:.2f}",
            annotation_position="top right",
            annotation_font_color="rgba(255,255,255,0.6)",
        )
    _add_sr_vlines(fig_iv, resistances, supports)

    iv_title = "Implied Volatility Smile" + ("  (avg across expiries)" if is_aggregated else "")
    fig_iv.update_layout(
        **LAYOUT_BASE,
        title=iv_title,
        xaxis_title="Strike",
        yaxis_title="Implied Volatility (%)",
    )
    fig_iv.update_xaxes(showgrid=True, gridcolor=GRID_CLR)
    fig_iv.update_yaxes(showgrid=True, gridcolor=GRID_CLR)
    st.plotly_chart(fig_iv, use_container_width=True)

# ── Right: Open Interest ──────────────────────────────────────────────────────
with chart_right:
    calls_oi = calls_f.sort_values("strike")
    puts_oi  = puts_f.sort_values("strike")

    fig_oi = go.Figure()

    fig_oi.add_trace(go.Bar(
        x=calls_oi["strike"],
        y=calls_oi["openInterest"].fillna(0).astype(int),
        name="Call OI",
        marker_color="#26a69a",
    ))
    fig_oi.add_trace(go.Bar(
        x=puts_oi["strike"],
        y=puts_oi["openInterest"].fillna(0).astype(int),
        name="Put OI",
        marker_color="#ef5350",
    ))
    if spot:
        fig_oi.add_vline(
            x=spot, line_dash="dash", line_color="rgba(255,255,255,0.4)",
            annotation_text=f"ATM {spot:.2f}",
            annotation_position="top right",
            annotation_font_color="rgba(255,255,255,0.6)",
        )
    for i, (strike, oi) in enumerate(resistances[:2]):
        fig_oi.add_vline(
            x=strike, line_dash="dot", line_color="#ef5350",
            annotation_text=f"R{i + 1}  {strike:.0f}  (OI {oi:,})",
            annotation_position="top left",
            annotation_font_color="#ef5350",
        )
    for i, (strike, oi) in enumerate(supports[:2]):
        fig_oi.add_vline(
            x=strike, line_dash="dot", line_color="#26a69a",
            annotation_text=f"S{i + 1}  {strike:.0f}  (OI {oi:,})",
            annotation_position="top right",
            annotation_font_color="#26a69a",
        )

    oi_title = "Open Interest by Strike" + ("  (aggregated)" if is_aggregated else "")
    fig_oi.update_layout(
        **LAYOUT_BASE,
        title=oi_title,
        xaxis_title="Strike",
        yaxis_title="Open Interest",
        barmode="group",
    )
    fig_oi.update_xaxes(showgrid=True, gridcolor=GRID_CLR)
    fig_oi.update_yaxes(showgrid=True, gridcolor=GRID_CLR)
    st.plotly_chart(fig_oi, use_container_width=True)

# ── Raw chain table ───────────────────────────────────────────────────────────
if show_table:
    st.divider()
    st.subheader("📋 Raw Option Chain")
    _cols = ["strike", "lastPrice", "bid", "ask", "volume", "openInterest", "impliedVolatility", "inTheMoney"]

    def _fmt_chain(df: pd.DataFrame) -> pd.DataFrame:
        df = df[[c for c in _cols if c in df.columns]].copy()
        if "impliedVolatility" in df.columns:
            df["impliedVolatility"] = (df["impliedVolatility"] * 100).round(2).astype(str) + "%"
        if "openInterest" in df.columns:
            df["openInterest"] = df["openInterest"].fillna(0).astype(int)
        return df

    tl, tr = st.columns(2)
    with tl:
        st.caption("**Calls**")
        st.dataframe(_fmt_chain(calls_f.sort_values("strike")),
                     use_container_width=True, hide_index=True)
    with tr:
        st.caption("**Puts**")
        st.dataframe(_fmt_chain(puts_f.sort_values("strike")),
                     use_container_width=True, hide_index=True)

# ── Footer ────────────────────────────────────────────────────────────────────
st.divider()
st.caption(f"Data via yfinance · Options cached 5 min · Last loaded: {datetime.now().strftime('%H:%M:%S')}")
