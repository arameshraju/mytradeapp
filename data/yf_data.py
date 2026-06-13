import streamlit as st
import yfinance as yf
import pandas as pd
import os
import json
from datetime import datetime, timedelta
from urllib.parse import urlencode
from urllib.request import urlopen

from data.db_manager import get_data_source_settings

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

SUPPORTED_DATA_SOURCES = ("alpha_vantage", "yahoo")
DEFAULT_PRIMARY_SOURCE = "alpha_vantage"


def resolve_ticker(raw: str) -> str:
    """Map common index names to their yfinance symbols."""
    return TICKER_ALIASES.get(raw.upper(), raw.upper())


def _alpha_api_key() -> str:
    secrets_value = ""
    try:
        secrets_value = st.secrets.get("ALPHAVANTAGE_API_KEY", "")
    except Exception:
        secrets_value = ""

    return (
        secrets_value
        or os.getenv("ALPHAVANTAGE_API_KEY", "")
        or os.getenv("ALPHA_KEY", "")
    ).strip()


def _alpha_symbol(ticker: str) -> str:
    return ticker.replace("^", "").replace("=F", "")


def _safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _period_start(period: str) -> datetime | None:
    now = datetime.now()
    mapping = {
        "1d": timedelta(days=1),
        "5d": timedelta(days=5),
        "1mo": timedelta(days=30),
        "3mo": timedelta(days=90),
        "6mo": timedelta(days=180),
        "1y": timedelta(days=365),
    }
    delta = mapping.get(period)
    return now - delta if delta else None


def _resolve_plan(primary_source: str | None = None, fallback_enabled: bool | None = None) -> dict:
    settings = {
        "market_data_primary": DEFAULT_PRIMARY_SOURCE,
        "market_data_fallback_enabled": True,
    }
    try:
        settings = get_data_source_settings()
    except Exception:
        pass

    primary = (primary_source or settings["market_data_primary"] or DEFAULT_PRIMARY_SOURCE).lower()
    if primary not in SUPPORTED_DATA_SOURCES:
        primary = DEFAULT_PRIMARY_SOURCE

    fallback = settings["market_data_fallback_enabled"] if fallback_enabled is None else bool(fallback_enabled)

    order = [primary]
    if fallback:
        alternate = "yahoo" if primary == "alpha_vantage" else "alpha_vantage"
        if alternate not in order:
            order.append(alternate)

    return {
        "primary_source": primary,
        "fallback_enabled": fallback,
        "source_order": order,
    }


@st.cache_data(ttl=60)
def _fetch_ohlcv_yahoo(ticker: str, period: str, interval: str) -> pd.DataFrame:
    df = yf.download(ticker, period=period, interval=interval, auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.dropna(inplace=True)
    return df


@st.cache_data(ttl=60)
def _fetch_last_price_yahoo(ticker: str):
    try:
        return float(yf.Ticker(ticker).fast_info.last_price)
    except Exception:
        return None


def _alpha_request(params: dict) -> tuple[dict | None, str | None]:
    api_key = _alpha_api_key()
    if not api_key:
        return None, "missing API key"

    url = "https://www.alphavantage.co/query?" + urlencode({**params, "apikey": api_key})
    try:
        with urlopen(url, timeout=12) as response:
            payload = response.read().decode("utf-8")
        data = json.loads(payload)
    except Exception as exc:
        return None, str(exc)

    if "Error Message" in data:
        return None, str(data.get("Error Message"))
    if "Information" in data:
        return None, str(data.get("Information"))
    if "Note" in data:
        return None, str(data.get("Note"))
    return data, None


@st.cache_data(ttl=60)
def _fetch_last_price_alpha(ticker: str):
    data, err = _alpha_request({
        "function": "GLOBAL_QUOTE",
        "symbol": _alpha_symbol(ticker),
    })
    if err:
        return None, err

    quote = data.get("Global Quote", {}) if data else {}
    price = _safe_float(quote.get("05. price"))
    return price, (None if price is not None else "empty quote")


@st.cache_data(ttl=60)
def _fetch_ohlcv_alpha(ticker: str, period: str, interval: str) -> tuple[pd.DataFrame, str | None]:
    symbol = _alpha_symbol(ticker)
    if interval == "1d":
        params = {
            "function": "TIME_SERIES_DAILY_ADJUSTED",
            "symbol": symbol,
            "outputsize": "full" if period in {"6mo", "1y"} else "compact",
        }
    else:
        interval_map = {
            "1m": "1min",
            "5m": "5min",
            "15m": "15min",
            "30m": "30min",
            "1h": "60min",
        }
        av_interval = interval_map.get(interval)
        if not av_interval:
            return pd.DataFrame(), f"unsupported Alpha Vantage interval: {interval}"
        params = {
            "function": "TIME_SERIES_INTRADAY",
            "symbol": symbol,
            "interval": av_interval,
            "outputsize": "full" if period in {"6mo", "1y"} else "compact",
            "adjusted": "true",
        }

    data, err = _alpha_request(params)
    if err:
        return pd.DataFrame(), err

    ts_key = None
    for key in (data or {}).keys():
        if "Time Series" in key:
            ts_key = key
            break
    if not ts_key:
        return pd.DataFrame(), "missing time series payload"

    rows = []
    for ts, bar in data[ts_key].items():
        rows.append(
            {
                "Date": pd.to_datetime(ts),
                "Open": _safe_float(bar.get("1. open")),
                "High": _safe_float(bar.get("2. high")),
                "Low": _safe_float(bar.get("3. low")),
                "Close": _safe_float(bar.get("4. close")),
                "Volume": _safe_float(bar.get("6. volume") or bar.get("5. volume")),
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return df, "empty time series"

    df.set_index("Date", inplace=True)
    df.sort_index(inplace=True)
    df.dropna(inplace=True)

    start = _period_start(period)
    if start is not None:
        df = df[df.index >= start]

    return df, (None if not df.empty else "empty after period filter")


@st.cache_data(ttl=60)
def fetch_ohlcv(ticker: str, period: str, interval: str) -> pd.DataFrame:
    """Download OHLCV data for a ticker and return a flat DataFrame."""
    return _fetch_ohlcv_yahoo(ticker, period, interval)


def fetch_ohlcv_with_source(
    ticker: str,
    period: str,
    interval: str,
    primary_source: str | None = None,
    fallback_enabled: bool | None = None,
) -> tuple[pd.DataFrame, dict]:
    plan = _resolve_plan(primary_source, fallback_enabled)
    attempts = []

    for source in plan["source_order"]:
        if source == "alpha_vantage":
            df, err = _fetch_ohlcv_alpha(ticker, period, interval)
            attempts.append({"source": source, "ok": not df.empty, "reason": err})
            if not df.empty:
                return df, {**plan, "source_used": source, "attempts": attempts}
        else:
            df = _fetch_ohlcv_yahoo(ticker, period, interval)
            attempts.append({"source": source, "ok": not df.empty, "reason": None if not df.empty else "empty dataframe"})
            if not df.empty:
                return df, {**plan, "source_used": source, "attempts": attempts}

    return pd.DataFrame(), {**plan, "source_used": None, "attempts": attempts}


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
    return _fetch_last_price_yahoo(ticker)


def fetch_last_price_with_source(
    ticker: str,
    primary_source: str | None = None,
    fallback_enabled: bool | None = None,
) -> tuple[float | None, dict]:
    plan = _resolve_plan(primary_source, fallback_enabled)
    attempts = []

    for source in plan["source_order"]:
        if source == "alpha_vantage":
            price, err = _fetch_last_price_alpha(ticker)
            attempts.append({"source": source, "ok": price is not None, "reason": err})
            if price is not None:
                return price, {**plan, "source_used": source, "attempts": attempts}
        else:
            price = _fetch_last_price_yahoo(ticker)
            attempts.append({"source": source, "ok": price is not None, "reason": None if price is not None else "empty quote"})
            if price is not None:
                return price, {**plan, "source_used": source, "attempts": attempts}

    return None, {**plan, "source_used": None, "attempts": attempts}
