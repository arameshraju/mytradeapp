"""
db_manager.py  —  SQLite persistence layer for the trading app.
Database file: trading_app.db (project root).
"""

import sqlite3
from pathlib import Path
from datetime import datetime

# ── Database location ─────────────────────────────────────────────────────────
_DB_PATH = Path(__file__).parent.parent / "trading_app.db"

_DEFAULT_WATCHLIST = [
    ("SPX",  "S&P 500 Index"),
    ("SPY",  "S&P 500 ETF"),
    ("NQ",   "Nasdaq 100 Futures"),
    ("QQQ",  "Nasdaq 100 ETF"),
    ("MSFT", "Microsoft"),
    ("AAPL", "Apple"),
    ("AMZN", "Amazon"),
    ("TSLA", "Tesla"),
    ("GS",   "Goldman Sachs"),
    ("BA",   "Boeing"),
]

_DEFAULT_SETTINGS = {
    "market_data_primary": "alpha_vantage",
    "market_data_fallback_enabled": "1",
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    """Create tables and seed the watchlist with defaults (if empty)."""
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS watchlist (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker       TEXT    NOT NULL UNIQUE,
                display_name TEXT    NOT NULL DEFAULT '',
                added_at     TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS portfolio (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker       TEXT    NOT NULL,
                quantity     REAL    NOT NULL,
                entry_price  REAL    NOT NULL,
                entry_date   TEXT    NOT NULL,
                added_at     TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS options_summary (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker         TEXT    NOT NULL,
                expiry_date    TEXT    NOT NULL,
                atm_iv_call    REAL,
                atm_iv_put     REAL,
                total_call_oi  INTEGER,
                total_put_oi   INTEGER,
                pc_ratio       REAL,
                fetched_at     TEXT    NOT NULL,
                UNIQUE(ticker, expiry_date) ON CONFLICT REPLACE
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key         TEXT PRIMARY KEY,
                value       TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );
        """)

        # Seed watchlist only when the table is empty
        count = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
        if count == 0:
            now = datetime.utcnow().isoformat()
            conn.executemany(
                "INSERT OR IGNORE INTO watchlist (ticker, display_name, added_at) VALUES (?, ?, ?)",
                [(t, d, now) for t, d in _DEFAULT_WATCHLIST],
            )

        now = datetime.utcnow().isoformat()
        conn.executemany(
            "INSERT OR IGNORE INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)",
            [(k, v, now) for k, v in _DEFAULT_SETTINGS.items()],
        )


# ── Watchlist ─────────────────────────────────────────────────────────────────

def get_watchlist() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, ticker, display_name, added_at FROM watchlist ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


def add_watchlist(ticker: str, display_name: str = "") -> None:
    ticker = ticker.strip().upper()
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (ticker, display_name, added_at) VALUES (?, ?, ?)",
            (ticker, display_name, datetime.utcnow().isoformat()),
        )


def remove_watchlist(ticker: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker.upper(),))


# ── Portfolio ─────────────────────────────────────────────────────────────────

def get_portfolio() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, ticker, quantity, entry_price, entry_date, added_at "
            "FROM portfolio ORDER BY added_at"
        ).fetchall()
    return [dict(r) for r in rows]


def add_portfolio(ticker: str, quantity: float, entry_price: float, entry_date: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO portfolio (ticker, quantity, entry_price, entry_date, added_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (ticker.strip().upper(), quantity, entry_price, entry_date, datetime.utcnow().isoformat()),
        )


def remove_portfolio(row_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM portfolio WHERE id = ?", (row_id,))


# ── Options Summary ───────────────────────────────────────────────────────────

def upsert_options_summary(
    ticker: str,
    expiry_date: str,
    atm_iv_call: float | None,
    atm_iv_put: float | None,
    total_call_oi: int | None,
    total_put_oi: int | None,
    pc_ratio: float | None,
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO options_summary
                (ticker, expiry_date, atm_iv_call, atm_iv_put,
                 total_call_oi, total_put_oi, pc_ratio, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, expiry_date) DO UPDATE SET
                atm_iv_call   = excluded.atm_iv_call,
                atm_iv_put    = excluded.atm_iv_put,
                total_call_oi = excluded.total_call_oi,
                total_put_oi  = excluded.total_put_oi,
                pc_ratio      = excluded.pc_ratio,
                fetched_at    = excluded.fetched_at
            """,
            (
                ticker.upper(), expiry_date,
                atm_iv_call, atm_iv_put,
                total_call_oi, total_put_oi,
                pc_ratio,
                datetime.utcnow().isoformat(),
            ),
        )


def get_options_summary(ticker: str | None = None) -> list[dict]:
    """Return saved options summary rows, optionally filtered by ticker."""
    with _connect() as conn:
        if ticker:
            rows = conn.execute(
                "SELECT * FROM options_summary WHERE ticker = ? ORDER BY expiry_date",
                (ticker.upper(),),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM options_summary ORDER BY ticker, expiry_date"
            ).fetchall()
    return [dict(r) for r in rows]


# ── App Settings ──────────────────────────────────────────────────────────────

def get_setting(key: str, default: str | None = None) -> str | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (key,),
        ).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, value, datetime.utcnow().isoformat()),
        )


def get_data_source_settings() -> dict:
    primary = get_setting("market_data_primary", _DEFAULT_SETTINGS["market_data_primary"])
    fallback_raw = get_setting(
        "market_data_fallback_enabled",
        _DEFAULT_SETTINGS["market_data_fallback_enabled"],
    )
    return {
        "market_data_primary": primary or _DEFAULT_SETTINGS["market_data_primary"],
        "market_data_fallback_enabled": str(fallback_raw) == "1",
    }


def save_data_source_settings(primary: str, fallback_enabled: bool) -> dict:
    primary_clean = (primary or _DEFAULT_SETTINGS["market_data_primary"]).strip().lower()
    if primary_clean not in ("alpha_vantage", "yahoo"):
        primary_clean = _DEFAULT_SETTINGS["market_data_primary"]

    set_setting("market_data_primary", primary_clean)
    set_setting("market_data_fallback_enabled", "1" if fallback_enabled else "0")
    return get_data_source_settings()


def reset_data_source_settings() -> dict:
    save_data_source_settings(
        _DEFAULT_SETTINGS["market_data_primary"],
        _DEFAULT_SETTINGS["market_data_fallback_enabled"] == "1",
    )
    return get_data_source_settings()
