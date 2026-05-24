import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime

from data.yf_data import resolve_ticker, fetch_ohlcv

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .block-container { padding-top: 1rem; }
    .metric-card {
        background: #1e222d;
        border-radius: 8px;
        padding: 12px 16px;
        border-left: 3px solid #38bdf8;
    }
    div[data-testid="stMetricValue"] { font-size: 1.4rem; font-weight: 700; }
    div[data-testid="stMetricDelta"] { font-size: 0.85rem; }
    .stDataFrame { border-radius: 8px; overflow: hidden; }
    h2, h3 { color: #e2e8f0; }
</style>
""", unsafe_allow_html=True)

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("⚙️ Settings")
    st.divider()

    _default_ticker = st.session_state.pop("selected_ticker", "ES=F")
    ticker_raw = st.text_input("Ticker Symbol", value=_default_ticker,
                           help="Examples: ES=F, GC=F, CL=F, SPY, AAPL, ^GSPC · Use SPX→^GSPC, NDX→^NDX, VIX→^VIX").upper()
    ticker = resolve_ticker(ticker_raw)

    period_map = {
        "1 Day": "1d", "5 Days": "5d", "1 Month": "1mo",
        "3 Months": "3mo", "6 Months": "6mo", "1 Year": "1y",
    }
    period_label = st.selectbox("Period", list(period_map.keys()), index=2)
    period = period_map[period_label]

    interval_map = {
        "1 Min": "1m", "5 Min": "5m", "15 Min": "15m",
        "30 Min": "30m", "1 Hour": "1h", "1 Day": "1d",
    }
    interval_label = st.selectbox("Interval", list(interval_map.keys()), index=4)
    interval = interval_map[interval_label]

    st.divider()
    st.subheader("📐 Indicators")
    show_ema20  = st.checkbox("EMA 20",  value=True)
    show_ema50  = st.checkbox("EMA 50",  value=True)
    show_vwap   = st.checkbox("VWAP",    value=True)
    show_bb     = st.checkbox("Bollinger Bands", value=False)
    rsi_period  = st.slider("RSI Period", 7, 21, 14)
    macd_fast   = st.slider("MACD Fast",  5, 20, 12)
    macd_slow   = st.slider("MACD Slow", 20, 40, 26)
    macd_signal = st.slider("MACD Signal", 5, 15, 9)

    st.divider()
    st.subheader("🎛️ Contract Specs")
    tick_value = st.number_input("Tick Value ($)", value=12.50, step=1.0)
    tick_size  = st.number_input("Tick Size", value=0.25, step=0.01, format="%.4f")


# ── Indicators ───────────────────────────────────────────────────────────────
def compute_rsi(series, n=14):
    delta = series.diff()
    gain  = delta.clip(lower=0).ewm(com=n - 1, min_periods=n).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=n - 1, min_periods=n).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def compute_macd(series, fast=12, slow=26, signal=9):
    ema_f  = series.ewm(span=fast, adjust=False).mean()
    ema_s  = series.ewm(span=slow, adjust=False).mean()
    macd   = ema_f - ema_s
    sig    = macd.ewm(span=signal, adjust=False).mean()
    return macd, sig, macd - sig

def compute_vwap(df):
    typical = (df["High"] + df["Low"] + df["Close"]) / 3
    cum_tp_vol = (typical * df["Volume"]).cumsum()
    cum_vol    = df["Volume"].cumsum().replace(0, np.nan)
    return cum_tp_vol / cum_vol

def compute_bollinger(series, n=20, std=2):
    mid  = series.rolling(n).mean()
    band = series.rolling(n).std()
    return mid, mid + std * band, mid - std * band


# ── Load Data ─────────────────────────────────────────────────────────────────
with st.spinner(f"Fetching {ticker} data…"):
    df = fetch_ohlcv(ticker, period, interval)

if df.empty:
    suggestion = ""
    if ticker_raw != ticker:
        suggestion = f"  (resolved **{ticker_raw}** → **{ticker}**)  "
    elif ticker_raw.upper() in ["SPX", "SPXW"]:
        suggestion = "  Try **^GSPC** for the S&P 500 index.  "
    st.error(f"⚠️ No data returned for **{ticker}**.{suggestion}Check the ticker symbol, period, or interval.")
    st.stop()

# Compute
df["RSI"]         = compute_rsi(df["Close"], rsi_period)
df["MACD"], df["MACDSig"], df["MACDHist"] = compute_macd(df["Close"], macd_fast, macd_slow, macd_signal)
df["EMA20"]       = df["Close"].ewm(span=20, adjust=False).mean()
df["EMA50"]       = df["Close"].ewm(span=50, adjust=False).mean()
df["VWAP"]        = compute_vwap(df)
df["BB_Mid"], df["BB_Up"], df["BB_Lo"] = compute_bollinger(df["Close"])

# Delta calc helper
def delta_str(val, prev_val):
    d = val - prev_val
    pct = d / prev_val * 100 if prev_val else 0
    return f"{d:+.2f} ({pct:+.2f}%)"

last  = float(df["Close"].iloc[-1])
prev  = float(df["Close"].iloc[-2]) if len(df) > 1 else last
chg   = last - prev
chg_pct = chg / prev * 100

# ── Header KPIs ───────────────────────────────────────────────────────────────
display_ticker = f"{ticker_raw} ({ticker})" if ticker_raw != ticker else ticker
st.title(f"📈  {display_ticker}  —  Trading Dashboard")
k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Last Price",   f"{last:.2f}",  f"{chg:+.2f} ({chg_pct:+.2f}%)")
k2.metric("Session High", f"{float(df['High'].iloc[-1]):.2f}")
k3.metric("Session Low",  f"{float(df['Low'].iloc[-1]):.2f}")
k4.metric("RSI (14)",     f"{float(df['RSI'].iloc[-1]):.1f}")
k5.metric("MACD",         f"{float(df['MACD'].iloc[-1]):.3f}")
k6.metric("Volume",       f"{int(df['Volume'].sum()):,}")

st.divider()

# ── Main Chart ────────────────────────────────────────────────────────────────
fig = make_subplots(
    rows=4, cols=1,
    shared_xaxes=True,
    row_heights=[0.50, 0.15, 0.18, 0.17],
    vertical_spacing=0.025,
    subplot_titles=("", "", "RSI", "MACD"),
)

# ── Row 1: Candlestick ────────────────────────────────────────────────────────
fig.add_trace(go.Candlestick(
    x=df.index,
    open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"],
    name="OHLCV",
    increasing=dict(line=dict(color="#26a69a"), fillcolor="#26a69a"),
    decreasing=dict(line=dict(color="#ef5350"), fillcolor="#ef5350"),
), row=1, col=1)

if show_ema20:
    fig.add_trace(go.Scatter(x=df.index, y=df["EMA20"], name="EMA 20",
                             line=dict(color="#f59e0b", width=1.3)), row=1, col=1)
if show_ema50:
    fig.add_trace(go.Scatter(x=df.index, y=df["EMA50"], name="EMA 50",
                             line=dict(color="#818cf8", width=1.3)), row=1, col=1)
if show_vwap:
    fig.add_trace(go.Scatter(x=df.index, y=df["VWAP"], name="VWAP",
                             line=dict(color="#ec4899", width=1.3, dash="dot")), row=1, col=1)
if show_bb:
    fig.add_trace(go.Scatter(x=df.index, y=df["BB_Up"], name="BB Upper",
                             line=dict(color="#94a3b8", width=1, dash="dash")), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["BB_Lo"], name="BB Lower",
                             line=dict(color="#94a3b8", width=1, dash="dash"),
                             fill="tonexty", fillcolor="rgba(148,163,184,0.07)"), row=1, col=1)

# ── Row 2: Volume ─────────────────────────────────────────────────────────────
bar_colors = [
    "#26a69a" if c >= o else "#ef5350"
    for c, o in zip(df["Close"], df["Open"])
]
fig.add_trace(go.Bar(
    x=df.index, y=df["Volume"],
    name="Volume", marker_color=bar_colors, showlegend=False,
), row=2, col=1)

# Volume MA
vol_ma = df["Volume"].rolling(20).mean()
fig.add_trace(go.Scatter(x=df.index, y=vol_ma, name="Vol MA20",
                         line=dict(color="#fbbf24", width=1.2)), row=2, col=1)

# ── Row 3: RSI ────────────────────────────────────────────────────────────────
fig.add_trace(go.Scatter(x=df.index, y=df["RSI"], name="RSI",
                         line=dict(color="#a78bfa", width=1.5)), row=3, col=1)
fig.add_hline(y=70, line_dash="dash", line_color="rgba(239,83,80,0.5)",  row=3, col=1)
fig.add_hline(y=30, line_dash="dash", line_color="rgba(38,166,154,0.5)", row=3, col=1)
fig.add_hline(y=50, line_dash="dot",  line_color="rgba(148,163,184,0.3)", row=3, col=1)
fig.add_hrect(y0=70, y1=100, fillcolor="red",   opacity=0.04, row=3, col=1)
fig.add_hrect(y0=0,  y1=30,  fillcolor="green", opacity=0.04, row=3, col=1)

# ── Row 4: MACD ───────────────────────────────────────────────────────────────
hist_colors = ["#26a69a" if v >= 0 else "#ef5350" for v in df["MACDHist"]]
fig.add_trace(go.Bar(x=df.index, y=df["MACDHist"], name="Histogram",
                     marker_color=hist_colors, showlegend=False), row=4, col=1)
fig.add_trace(go.Scatter(x=df.index, y=df["MACD"],    name="MACD",
                         line=dict(color="#38bdf8", width=1.5)), row=4, col=1)
fig.add_trace(go.Scatter(x=df.index, y=df["MACDSig"], name="Signal",
                         line=dict(color="#fb923c", width=1.5)), row=4, col=1)

# ── Layout ────────────────────────────────────────────────────────────────────
fig.update_layout(
    height=870,
    xaxis_rangeslider_visible=False,
    template="plotly_dark",
    paper_bgcolor="#0f1117",
    plot_bgcolor="#0f1117",
    legend=dict(orientation="h", y=1.01, x=0, bgcolor="rgba(0,0,0,0)"),
    margin=dict(l=50, r=30, t=30, b=30),
)
for row in range(1, 5):
    fig.update_xaxes(showgrid=True, gridcolor="#1e222d", row=row, col=1)
    fig.update_yaxes(showgrid=True, gridcolor="#1e222d", row=row, col=1)

fig.update_yaxes(title_text="Price",  row=1, col=1)
fig.update_yaxes(title_text="Volume", row=2, col=1)
fig.update_yaxes(title_text="RSI",    row=3, col=1, range=[0, 100])
fig.update_yaxes(title_text="MACD",   row=4, col=1)

st.plotly_chart(fig, use_container_width=True)

st.divider()

# ── P&L Trade Tracker ─────────────────────────────────────────────────────────
st.subheader("📋 P&L Trade Tracker")

if "trades" not in st.session_state:
    st.session_state.trades = []

left, right = st.columns([1, 2], gap="large")

with left:
    st.markdown("#### ➕ Log a Trade")
    t_date   = st.date_input("Date", value=datetime.today())
    t_side   = st.selectbox("Side", ["Long", "Short"])
    t_qty    = st.number_input("Contracts / Qty", min_value=1, value=1)
    t_entry  = st.number_input("Entry Price", value=round(last, 2), step=0.25)
    t_exit   = st.number_input("Exit Price",  value=round(last, 2), step=0.25)
    t_fee    = st.number_input("Commission ($)", value=4.00, step=0.50)

    if st.button("Add Trade", type="primary", use_container_width=True):
        raw_ticks = (t_exit - t_entry) / tick_size
        ticks = raw_ticks if t_side == "Long" else -raw_ticks
        pnl   = ticks * tick_value * t_qty - t_fee
        st.session_state.trades.append({
            "Date":       str(t_date),
            "Side":       t_side,
            "Qty":        t_qty,
            "Entry":      t_entry,
            "Exit":       t_exit,
            "Ticks":      round(ticks, 2),
            "Commission": t_fee,
            "Net P&L":    round(pnl, 2),
        })
        st.success(f"Trade logged  |  Net P&L: ${pnl:,.2f}")

with right:
    if st.session_state.trades:
        tdf = pd.DataFrame(st.session_state.trades)
        total   = tdf["Net P&L"].sum()
        winners = (tdf["Net P&L"] > 0).sum()
        losers  = (tdf["Net P&L"] < 0).sum()
        wr      = winners / len(tdf) * 100
        avg_win  = tdf.loc[tdf["Net P&L"] > 0, "Net P&L"].mean() if winners else 0
        avg_loss = tdf.loc[tdf["Net P&L"] < 0, "Net P&L"].mean() if losers  else 0
        rr = abs(avg_win / avg_loss) if avg_loss else float("inf")

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Net P&L",   f"${total:,.2f}")
        m2.metric("Trades",    len(tdf))
        m3.metric("Win Rate",  f"{wr:.1f}%")
        m4.metric("Avg Win",   f"${avg_win:,.2f}")
        m5.metric("Reward/Risk", f"{rr:.2f}x" if rr != float("inf") else "∞")

        # Styled table
        def color_pnl(val):
            if isinstance(val, (int, float)):
                return "color:#26a69a" if val > 0 else ("color:#ef5350" if val < 0 else "")
            return ""

        st.dataframe(
            tdf.style.map(color_pnl, subset=["Net P&L", "Ticks"]),
            use_container_width=True, hide_index=True,
        )

        # Cumulative P&L chart
        tdf["Cumulative"] = tdf["Net P&L"].cumsum()
        line_color = "#26a69a" if total >= 0 else "#ef5350"
        fig_pnl = go.Figure()
        fig_pnl.add_trace(go.Scatter(
            y=tdf["Cumulative"], mode="lines+markers",
            line=dict(color=line_color, width=2),
            fill="tozeroy", fillcolor=f"rgba({'38,166,154' if total >= 0 else '239,83,80'},0.1)",
            name="Cum. P&L",
        ))
        fig_pnl.add_hline(y=0, line_color="rgba(148,163,184,0.4)", line_dash="dash")
        fig_pnl.update_layout(
            title="Cumulative Net P&L", template="plotly_dark",
            paper_bgcolor="#0f1117", plot_bgcolor="#0f1117",
            height=220, margin=dict(l=30, r=20, t=35, b=20), showlegend=False,
        )
        st.plotly_chart(fig_pnl, use_container_width=True)

        if st.button("🗑️ Clear All Trades", use_container_width=True):
            st.session_state.trades = []
            st.rerun()
    else:
        st.info("No trades logged yet. Use the form on the left to add your first trade.")

# ── Footer ────────────────────────────────────────────────────────────────────
st.divider()
st.caption(f"Data via yfinance · Auto-refreshes every 60s · Last loaded: {datetime.now().strftime('%H:%M:%S')}")
