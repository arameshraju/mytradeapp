# 📈 Trading Dashboard — Streamlit

Interactive trading dashboard with OHLCV candlesticks, technical indicators,
volume/order flow, and a P&L trade tracker.

## Features
| Panel | Details |
|---|---|
| **Candlestick** | OHLCV + EMA 20/50 + VWAP + Bollinger Bands |
| **Volume** | Directional bars (green/red) + 20-bar MA |
| **RSI** | Configurable period · OB/OS zones |
| **MACD** | Histogram + signal line · configurable fast/slow/signal |
| **P&L Tracker** | Log trades, compute net P&L (with commission), win rate, R:R |

## Quickstart

```bash
# 1. Create a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the dashboard
streamlit run app.py
```

Opens at **http://localhost:8501**

## Tickers to try
| Instrument | Ticker |
|---|---|
| E-mini S&P 500 | `ES=F` |
| Gold Futures | `GC=F` |
| Crude Oil | `CL=F` |
| S&P 500 ETF | `SPY` |
| Nasdaq ETF | `QQQ` |

## Sidebar Controls
- **Ticker / Period / Interval** — data selection
- **Indicator toggles** — EMA, VWAP, Bollinger Bands
- **Indicator params** — RSI period, MACD fast/slow/signal
- **Contract specs** — tick value & size for P&L calculation

## P&L Tracker
Enter entry/exit prices and the app auto-computes ticks, gross P&L, and net P&L
after commission. All trades persist in session state and a cumulative equity
curve is plotted live.
