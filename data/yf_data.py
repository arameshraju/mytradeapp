import streamlit as st
import yfinance as yf
import pandas as pd

# ── Ticker aliases (common names → yfinance symbols) ─────────────────────────
TICKER_ALIASES = {
    "SPX":  "^GSPC",
    "NDX":  "^NDX",
    "NQ":   "NQ=F",
    "ES":   "ES=F",
    "RTY":  "RTY=F",
    "DJI":  "^DJI",
    "DJIA": "^DJI",
    "VIX":  "^VIX",
    "RUT":  "^RUT",
    "FTSE": "^FTSE",
    "DAX":  "^GDAXI",
}


def resolve_ticker(raw: str) -> str:
    """Map common index names to their yfinance symbols."""
    return TICKER_ALIASES.get(raw.upper(), raw.upper())


@st.cache_data(ttl=60)
def fetch_ohlcv(ticker: str, period: str, interval: str) -> pd.DataFrame:
    """Download OHLCV data for a ticker and return a flat DataFrame."""
    df = yf.download(ticker, period=period, interval=interval,
                     auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.dropna(inplace=True)
    return df


@st.cache_data(ttl=300)
def fetch_option_expirations(ticker: str) -> list:
    """Return the list of available option expiration date strings for a ticker."""
    try:
        return list(yf.Ticker(ticker).options)
    except Exception:
        return []


@st.cache_data(ttl=300)
def fetch_option_chain(ticker: str, expiry: str):
    """Return (calls_df, puts_df) for a given ticker and expiry date."""
    chain = yf.Ticker(ticker).option_chain(expiry)
    return chain.calls, chain.puts


@st.cache_data(ttl=60)
def fetch_last_price(ticker: str):
    """Return the latest market price for a ticker, or None on failure."""
    try:
        return float(yf.Ticker(ticker).fast_info.last_price)
    except Exception:
        return None
