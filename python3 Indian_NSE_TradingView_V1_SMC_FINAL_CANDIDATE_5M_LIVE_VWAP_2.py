import os
import time
import json
import math
import html
import re
import difflib
import threading
from urllib.parse import quote
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from dotenv import load_dotenv

from angel_client import get_client, get_feed_token, get_auth_token
import paper_trade

# ============================================================
# INDIAN NSE - TRADINGVIEW V1 SMC MIRROR
# Angel One SmartAPI WebSocket + Yahoo warm-up + Telegram
#
# Signal only. NO automatic order placement.
#
# Same sequential logic as TradingView V1:
# Liquidity Sweep -> Displacement -> MSS -> FVG/OB
# -> Retest -> Confirmation -> Volume -> Entry
#
# Scan mode: ALL TIME / 24x7. No clock-time or weekday restriction.
# Timestamps are Asia/Kolkata (IST).
# ============================================================

load_dotenv()
IST = ZoneInfo("Asia/Kolkata")

# ============================================================
# TELEGRAM
# ============================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

SENT_SIGNALS = set()

# GitHub Actions starts a fresh process every 15 minutes, so SENT_SIGNALS is empty
# in each new run. A signal candle that closed BEFORE this run started was already
# handled by the previous run -> skip it here, otherwise the same alert is sent twice.
try:
    RUN_START_EPOCH = float(os.getenv("SCAN_RUN_START_EPOCH", "") or time.time())
except ValueError:
    RUN_START_EPOCH = time.time()


def candle_closed_before_run_start(result):
    try:
        start = datetime.strptime(result["candle_time"][:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
    except (KeyError, ValueError, TypeError):
        return False
    return (start + timedelta(minutes=5)).timestamp() < RUN_START_EPOCH

# Active signal lifecycle:
# Entry alert -> monitor live price -> TP1 / TP2 / SL alert.
ACTIVE_TRADES = {}
TRADE_LOCK = threading.Lock()

# Paper trading: every SHORT alert becomes a virtual Rs 1 lakh trade; exits are
# tracked on closed 5m candles with the backtest rule (see paper_trade.py).
PAPER_TRADE_SHORT = True

STOP_LOSS_PERCENT = 1.5   # backtest 180d: SL 1.5% beat 1%
TP1_PERCENT = 2.0
TP2_PERCENT = 2.0

def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram: NOT CONFIGURED")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        response = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        response.raise_for_status()
        print("Telegram: ENTRY ALERT SENT")
        return True
    except Exception as e:
        print(f"Telegram ERROR: {e}")
        return False


def signal_key(result):
    return (
        result["symbol"],
        result["signal"],
        result["candle_time"],
    )


def build_telegram_message(result):
    """Detailed LONG/SHORT entry alert with 1% SL and 2% TP."""
    def h(value):
        return html.escape(str(value), quote=False)
    direction = result["signal"]
    emoji = "🟢" if direction == "LONG" else "🔴"
    symbol = result["symbol"].replace("-EQ", "")
    entry = float(result.get("entry", 0))

    if direction == "LONG":
        sl = entry * (1.0 - STOP_LOSS_PERCENT / 100.0)
        tp1 = entry * (1.0 + TP1_PERCENT / 100.0)
        tp2 = entry * (1.0 + TP2_PERCENT / 100.0)
    else:
        sl = entry * (1.0 + STOP_LOSS_PERCENT / 100.0)
        tp1 = entry * (1.0 - TP1_PERCENT / 100.0)
        tp2 = entry * (1.0 - TP2_PERCENT / 100.0)

    risk = abs(entry - sl)
    reward1 = abs(tp1 - entry)
    reward2 = abs(tp2 - entry)
    stages = result.get("stage_times", {}) or {}

    def tm(key):
        x = stages.get(key, "—")
        return x[11:19] if isinstance(x, str) and len(x) >= 19 else "—"

    zone = result.get("zone_type", "—")
    disclaimer = (
        "⚠️ <b>Educational Purpose Only</b>\n"
        "Not financial/trading advice. Trading involves risk. "
        "Do your own analysis and risk management. "
        "Signal only — no automatic order placement."
    )

    return (
        f"{emoji} <b>NEW TRADE ALERT — {h(direction)}</b>\n\n"
        f"<b>Setup ID:</b>\n"
        f"<code>SETUP-{symbol}-{result.get('candle_time','').replace(' ','_').replace(':','')}</code>\n\n"
        f"<b>Instrument:</b> {symbol}\n"
        f"<b>Direction:</b> {direction}\n"
        f"<b>Timeframe:</b> 5m\n"
        f"<b>{'Bullish' if direction == 'LONG' else 'Bearish'} • {zone}</b>\n\n"

        f"<b>SMC CONFIRMATION</b>\n"
        f"✅ Liquidity Sweep — {result.get('sweep_type', '—')}\n"
        f"✅ Displacement — {float(result.get('displacement_atr', 0)):.2f} ATR\n"
        f"✅ MSS — {float(result.get('mss_level', 0)):.2f}\n"
        f"✅ Zone — {zone}\n"
        f"✅ Retest — PASS\n"
        f"✅ Confirmation — PASS\n"
        f"✅ Volume — {float(result.get('volume', 0)):.0f} > SMA {float(result.get('volume_sma', 0)):.0f}\n"
        f"{'✅' if USE_RSI_FILTER else 'ℹ️'} RSI(14) — {float(result.get('rsi_5m', 0)):.2f}{' ' + result.get('rsi_condition', '') if USE_RSI_FILTER else ' (filter OFF)'}\n"
        f"{'✅ Nifty Daily Trend — UP' if direction == 'LONG' and USE_NIFTY_TREND_LONG else '⏸ Nifty Trend Filter — not used for SHORT' if USE_NIFTY_TREND_LONG else '⏸ Nifty Trend Filter — OFF'}\n"
        f"✅ VWAP — ₹{float(result.get('vwap', 0)):.2f} ({result.get('vwap_condition', '')})\n"
        f"⏸ Daily MACD Filter — OFF\n\n"

        f"<b>TRADE PLAN</b>\n"
        f"Entry: ₹{entry:.2f}\n"
        f"🛑 SL: ₹{sl:.2f} (-{STOP_LOSS_PERCENT:.2f}%)\n"
        f"🎯 TP: ₹{tp2:.2f} (+{TP2_PERCENT:.2f}%)\n\n"
        f"Risk: ₹{risk:.2f}\n"
        f"Reward TP: ₹{reward2:.2f}\n"
        f"R:R: 1:{TP2_PERCENT / STOP_LOSS_PERCENT:.2f}\n"
        f"Current Price: ₹{entry:.2f}\n"
        f"Status: <b>OPEN — Monitoring SL / TP1 / TP2</b>\n\n"

        f"<b>TIMELINE</b>\n"
        f"Detection: {h(result.get('candle_time', '—'))}\n"
        f"MSS: {tm('MSS')}\n"
        f"Liquidity Sweep: {tm('SWEEP')}\n"
        f"Order Block: {tm('OB')}\n"
        f"FVG: {tm('FVG')}\n"
        f"Retest: {tm('RETEST')}\n"
        f"Confirmation: {tm('CONFIRMATION')}\n"
        f"TP1: ⬜\n"
        f"TP2: ⬜\n"
        f"SL: ⬜\n\n"

        f"{'📝 <b>PAPER TRADE: ON</b> — virtual ₹1,00,000, exit alert will follow\n' if PAPER_TRADE_SHORT and direction == 'SHORT' else ''}"
        f"<b>Strategy:</b> TradingView V1 SMC Mirror • 5m\n"
        f"<b>Mode:</b> Signal Only — No Automatic Order\n\n"
        f"{disclaimer}"
    )


def build_exit_message(trade, event, price):
    """Short TP/SL lifecycle alert. No disclaimer by design."""
    direction = trade["signal"]
    symbol = trade["symbol"].replace("-EQ", "")
    entry = trade["entry"]

    if event == "TP1":
        emoji = "🎯"
        title = "TP1 HIT"
        level = trade["tp1"]
        move = f"+{TP1_PERCENT:.2f}%"
        status = "TP1 HIT — Monitoring TP2"
    elif event == "TP2":
        emoji = "🚀"
        title = "TP2 HIT"
        level = trade["tp2"]
        move = f"+{TP2_PERCENT:.2f}%"
        status = "TP2 HIT — Trade Closed"
    else:
        emoji = "🛑"
        title = "SL HIT"
        level = trade["sl"]
        move = f"-{STOP_LOSS_PERCENT:.2f}%"
        status = "SL HIT — Trade Closed"

    return (
        f"{emoji} <b>{title} — {direction}</b>\n\n"
        f"<b>Instrument:</b> {symbol}\n"
        f"<b>Entry:</b> ₹{entry:.2f}\n"
        f"<b>{title.split(' ')[0]}:</b> ₹{level:.2f}\n"
        f"<b>Hit Price:</b> ₹{price:.2f}\n"
        f"<b>Move:</b> {move}\n\n"
        f"<b>Status:</b> {status}"
    )


def register_active_trade(result):
    """Register a newly alerted signal for live TP/SL monitoring."""
    token = str(result["token"])
    entry = float(result["entry"])
    direction = result["signal"]

    if direction == "LONG":
        sl = entry * (1.0 - STOP_LOSS_PERCENT / 100.0)
        tp1 = entry * (1.0 + TP1_PERCENT / 100.0)
        tp2 = entry * (1.0 + TP2_PERCENT / 100.0)
    else:
        sl = entry * (1.0 + STOP_LOSS_PERCENT / 100.0)
        tp1 = entry * (1.0 - TP1_PERCENT / 100.0)
        tp2 = entry * (1.0 - TP2_PERCENT / 100.0)

    with TRADE_LOCK:
        ACTIVE_TRADES[token] = {
            "token": token,
            "symbol": result["symbol"],
            "signal": direction,
            "entry": entry,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp1_hit": False,
            "candle_time": result.get("candle_time", ""),
        }


def get_live_price_for_token(token):
    with LIVE_LOCK:
        state = LIVE_CANDLE_STATE.get(str(token))
        if state is None:
            return None
        try:
            return float(state["close"])
        except Exception:
            return None


def monitor_active_trades():
    """Check live WebSocket prices and send separate TP1/TP2/SL alerts."""
    with TRADE_LOCK:
        trades = list(ACTIVE_TRADES.items())

    for token, trade in trades:
        price = get_live_price_for_token(token)
        if price is None:
            continue

        direction = trade["signal"]
        event = None

        with TRADE_LOCK:
            current = ACTIVE_TRADES.get(token)
            if current is None:
                continue

            if direction == "LONG":
                if price <= current["sl"]:
                    event = "SL"
                elif price >= current["tp2"]:
                    event = "TP2"
            else:
                if price >= current["sl"]:
                    event = "SL"
                elif price <= current["tp2"]:
                    event = "TP2"

            if event == "TP1":
                current["tp1_hit"] = True
            elif event in ("TP2", "SL"):
                ACTIVE_TRADES.pop(token, None)

        if event:
            msg = build_exit_message(trade, event, price)
            send_telegram_message(msg)
            print(f"TRADE ALERT: {trade['symbol']} {direction} {event} @ {price:.2f}")


# ============================================================
# SETTINGS
# ============================================================
SCAN_START_HOUR = 9
SCAN_START_MINUTE = 15
SCAN_END_HOUR = 15
SCAN_END_MINUTE = 10

# Scan once per 15-minute candle. Live candles are built locally from Angel WebSocket ticks.
API_DELAY_SECONDS = 0.05
SCAN_INTERVAL_MINUTES = 5
QUIET_STOCK_LOGS = True
PROGRESS_EVERY = 25
ENTRY_START_HOUR = 10    # backtest 180d: 09:30-10:00 entries lost money
ENTRY_START_MINUTE = 0
HIST_RATE_COOLDOWN_SECONDS = 90.0  # retained only for legacy helper; scanner does not call Angel historical API

# Universe: ONLY the user-provided stock_names.txt watchlist.
MAX_STOCKS = 0  # 0 = all matched watchlist stocks
# Persist the market-cap-filtered universe so Railway restarts do NOT
# re-query NSE for the same 5,000 Cr filter repeatedly on the same day.
WATCHLIST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_names.txt")

CANDLE_LIMIT_5M = 250
DAILY_HISTORY_DAYS = 450

SWING_LEFT = 2
SWING_RIGHT = 2

ATR_LENGTH = 14
MINIMUM_DISPLACEMENT_ATR = 1.0

USE_FVG = False   # backtest 180d: FVG zone OFF, Order Block only
USE_OB = True
OB_LOOKBACK = 5

USE_VOLUME_FILTER = True
VOLUME_LENGTH = 20

# Session VWAP filter: LONG must be above VWAP, SHORT must be below VWAP.
USE_VWAP_FILTER = True

# Added from the user's Chartink reference as extra confirmation.
USE_RSI_FILTER = False   # backtest 180d: RSI filter OFF was better
RSI_LENGTH = 14
RSI_LONG_MIN = 50.0
RSI_SHORT_MAX = 50.0

USE_DAILY_MACD_FILTER = False
DAILY_MACD_FAST = 12
DAILY_MACD_SLOW = 26
DAILY_MACD_SIGNAL = 9

# Same as TradingView V1 default: OFF.
USE_HTF_FILTER = False
HIGHER_TIMEFRAME = "ONE_HOUR"
HIGHER_TF_EMA_LENGTH = 50


SETUP_EXPIRY_BARS = 12

# LONG only when NIFTY 50 daily trend is UP (yesterday's close > daily EMA20).
# SHORT is not filtered. Backtest 180d: this removed most losing LONGs.
USE_NIFTY_TREND_LONG = True
NIFTY_DAILY_EMA = 20
NIFTY_YAHOO_TICKER = "^NSEI"

NSE_BASE_URL = "https://www.nseindia.com"
INSTRUMENT_URL = (
    "https://margincalculator.angelone.in/"
    "OpenAPI_File/files/OpenAPIScripMaster.json"
)

# ============================================================
# LIVE MARKET DATA ENGINE
# Angel WebSocket -> local 15-minute OHLCV candles
# ============================================================
LOCAL_5M_CACHE = {}
LIVE_CANDLE_STATE = {}
LIVE_LOCK = threading.RLock()
WATCHLIST_BY_TOKEN = {}
WEBSOCKET_INSTANCE = None
WEBSOCKET_THREAD = None
WEBSOCKET_STARTED = False

YAHOO_BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"
YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"
}


# ============================================================
# INSTRUMENT MASTER
# ============================================================
def load_instruments():
    print("Downloading Angel One instrument master...")

    response = requests.get(INSTRUMENT_URL, timeout=30)
    response.raise_for_status()
    data = response.json()

    if not isinstance(data, list):
        raise ValueError("Invalid Angel One instrument master")

    return data


def build_nse_equity_universe(instruments):
    """Return all NSE cash-equity -EQ symbols with Angel tokens."""
    stocks = []
    for item in instruments:
        if not isinstance(item, dict):
            continue
        exch = str(item.get("exch_seg", "")).strip().upper()
        symbol = str(item.get("symbol", "")).strip().upper()
        token = str(item.get("token", "")).strip()
        name = str(item.get("name", "")).strip().upper()
        if exch in ("NSE_CM", "NSE") and symbol.endswith("-EQ") and token:
            stocks.append({
                "symbol": symbol,
                "name": name or symbol[:-3],
                "token": token,
                "market_cap_cr": None,
            })
    unique = {x["symbol"]: x for x in stocks}
    return sorted(unique.values(), key=lambda x: x["symbol"])


def _norm_name(value):
    s = str(value or "").upper().strip()
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    # Common legal/company suffixes are ignored for matching.
    s = re.sub(r"\b(LTD|LIMITED|PRIVATE|PVT|PLC|INC|CORPORATION|CORP|COMPANY|CO)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def load_stock_name_watchlist(path=WATCHLIST_FILE):
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Watchlist file not found: {path}. Put stock_names.txt beside this Python file."
        )
    names = []
    seen = set()
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            name = line.strip()
            if not name:
                continue
            key = _norm_name(name)
            if key and key not in seen:
                seen.add(key)
                names.append(name)
    if not names:
        raise RuntimeError("stock_names.txt is empty.")
    return names


def build_watchlist_from_names(instruments, requested_names):
    """Resolve ONLY the supplied Stock Name list to NSE-EQ Angel tokens."""
    universe = build_nse_equity_universe(instruments)
    by_name = {}
    for item in universe:
        for candidate in (item.get("name", ""), item.get("symbol", "").replace("-EQ", "")):
            n = _norm_name(candidate)
            if n:
                by_name.setdefault(n, []).append(item)

    result = []
    used_symbols = set()
    unmatched = []
    for requested in requested_names:
        q = _norm_name(requested)
        candidates = by_name.get(q, [])
        chosen = candidates[0] if candidates else None

        if chosen is None:
            # Company-name feeds can differ slightly from Angel's short name.
            keys = list(by_name.keys())
            close = difflib.get_close_matches(q, keys, n=3, cutoff=0.55)
            for k in close:
                if by_name[k]:
                    chosen = by_name[k][0]
                    break

        if chosen is None:
            unmatched.append(requested)
            continue
        if chosen["symbol"] not in used_symbols:
            item = dict(chosen)
            item["watchlist_name"] = requested
            item["market_cap_cr"] = None
            result.append(item)
            used_symbols.add(chosen["symbol"])

    print("=" * 78)
    print(f"WATCHLIST FILE : {Path(WATCHLIST_FILE).name}")
    print(f"REQUESTED NAMES: {len(requested_names)}")
    print(f"MATCHED STOCKS : {len(result)}")
    print(f"UNMATCHED      : {len(unmatched)}")
    if unmatched:
        print("Unmatched names (first 30):")
        for x in unmatched[:30]:
            print(f"  - {x}")
    print("ONLY matched watchlist stocks will be scanned.")
    print("=" * 78)
    if not result:
        raise RuntimeError("No Stock Name from stock_names.txt could be matched to Angel NSE-EQ instruments.")
    return result


# ============================================================
# HISTORICAL API RATE LIMIT / CIRCUIT BREAKER
# ============================================================
class HistoricalRateLimitError(RuntimeError):
    """Raised when Angel One historical candle API reports access-rate blocking."""
    pass


_HIST_RATE_LOCK = __import__("threading").Lock()
_HIST_LAST_REQUEST_MONO = 0.0
_HIST_BLOCKED_UNTIL_MONO = 0.0
HIST_MIN_INTERVAL_SECONDS = 1.05


def _wait_for_historical_slot():
    """Serialize historical requests and respect the local circuit breaker."""
    global _HIST_LAST_REQUEST_MONO

    with _HIST_RATE_LOCK:
        now = time.monotonic()

        # If the previous request was blocked, do not hammer the endpoint.
        if now < _HIST_BLOCKED_UNTIL_MONO:
            wait = _HIST_BLOCKED_UNTIL_MONO - now
            print(f"Historical API cooldown active. Waiting {wait:.0f}s...")
            time.sleep(wait)
            now = time.monotonic()

        gap = now - _HIST_LAST_REQUEST_MONO
        if gap < HIST_MIN_INTERVAL_SECONDS:
            time.sleep(HIST_MIN_INTERVAL_SECONDS - gap)

        _HIST_LAST_REQUEST_MONO = time.monotonic()


def _mark_historical_rate_limit():
    """Activate the circuit breaker after an Angel historical API rate-limit response."""
    global _HIST_BLOCKED_UNTIL_MONO
    _HIST_BLOCKED_UNTIL_MONO = (
        time.monotonic() + HIST_RATE_COOLDOWN_SECONDS
    )

# ============================================================
# HISTORICAL CANDLES
# ============================================================
def fetch_candles(client, token, interval, days):
    """
    Fetch historical candles with conservative global pacing.

    Important: an Angel One access-rate/403 response is NOT retried.
    Retrying the same blocked request only increases the rate-limit pressure.
    The whole scan pass is paused instead.
    """
    now_ist = datetime.now(IST)
    from_dt = now_ist - timedelta(days=days)
    params = {
        "exchange": "NSE",
        "symboltoken": str(token),
        "interval": interval,
        "fromdate": from_dt.strftime("%Y-%m-%d %H:%M"),
        "todate": now_ist.strftime("%Y-%m-%d %H:%M"),
    }

    # One normal attempt. The global limiter controls every historical call.
    _wait_for_historical_slot()

    try:
        response = client.getCandleData(params)
        if response and response.get("status"):
            rows = response.get("data") or []
            if not rows:
                raise ValueError("No candle data returned")

            df = pd.DataFrame(
                rows,
                columns=["time", "open", "high", "low", "close", "volume"],
            )
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")

            df["time"] = pd.to_datetime(
                df["time"], errors="coerce", utc=True
            )
            return (
                df.dropna()
                .sort_values("time")
                .drop_duplicates("time")
                .reset_index(drop=True)
            )

        message = (
            response.get("message", "Unknown candle API error")
            if response else "Empty response"
        )
        error_text = str(message)

    except Exception as e:
        error_text = str(e)

    lower = error_text.lower()
    if (
        "access denied because of exceeding access rate" in lower
        or "exceeding access rate" in lower
        or "rate limit" in lower
        or "too many requests" in lower
        or "http 403" in lower
        or "403" in lower
    ):
        _mark_historical_rate_limit()
        raise HistoricalRateLimitError(
            f"Angel One historical API rate limit: {error_text}"
        )

    # Non-rate-limit errors get one delayed retry only.
    print(
        f"Historical API non-rate-limit error for token {token} ({interval}): "
        f"{error_text}. Retrying once after 5s..."
    )
    time.sleep(5.0)
    _wait_for_historical_slot()

    try:
        response = client.getCandleData(params)
        if response and response.get("status"):
            rows = response.get("data") or []
            if not rows:
                raise ValueError("No candle data returned")

            df = pd.DataFrame(
                rows,
                columns=["time", "open", "high", "low", "close", "volume"],
            )
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")

            df["time"] = pd.to_datetime(
                df["time"], errors="coerce", utc=True
            )
            return (
                df.dropna()
                .sort_values("time")
                .drop_duplicates("time")
                .reset_index(drop=True)
            )

        retry_message = (
            response.get("message", "Unknown candle API error")
            if response else "Empty response"
        )
        raise RuntimeError(str(retry_message))

    except Exception as e:
        lower = str(e).lower()
        if (
            "access denied because of exceeding access rate" in lower
            or "exceeding access rate" in lower
            or "rate limit" in lower
            or "too many requests" in lower
            or "403" in lower
        ):
            _mark_historical_rate_limit()
            raise HistoricalRateLimitError(
                f"Angel One historical API rate limit: {e}"
            )
        raise RuntimeError(str(e))


# ============================================================
# YAHOO WARM-UP / DAILY DATA
# Angel Historical API is intentionally NOT used by the scanner.
# Yahoo is used only for indicator warm-up; live data comes from Angel WebSocket.
# ============================================================
def _yahoo_chart(symbol, interval, period1, period2=None):
    ticker = str(symbol).upper().replace("-EQ", "") + ".NS"
    url = YAHOO_BASE_URL + quote(ticker, safe="")
    params = {"interval": interval, "period1": int(period1), "events": "history", "includePrePost": "false"}
    if period2 is not None:
        params["period2"] = int(period2)
    response = requests.get(url, params=params, headers=YAHOO_HEADERS, timeout=20)
    response.raise_for_status()
    payload = response.json()
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        raise RuntimeError(f"Yahoo returned no data for {ticker}")
    r = result[0]
    timestamps = r.get("timestamp") or []
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0]
    rows = []
    for i, ts in enumerate(timestamps):
        try:
            o = q.get("open", [])[i]
            h = q.get("high", [])[i]
            l = q.get("low", [])[i]
            c = q.get("close", [])[i]
            v = q.get("volume", [])[i]
            if None in (o, h, l, c, v):
                continue
            rows.append({"time": pd.to_datetime(int(ts), unit="s", utc=True),
                         "open": float(o), "high": float(h), "low": float(l),
                         "close": float(c), "volume": float(v)})
        except (IndexError, TypeError, ValueError):
            continue
    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError(f"Yahoo returned empty candles for {ticker}")
    return df.sort_values("time").drop_duplicates("time").reset_index(drop=True)


def yahoo_5m_warmup(stock):
    # Yahoo permits recent 5-minute intraday history; 30 days gives ample warm-up for
    # the 14 ATR/RSI, 20 volume SMA and the V1 state machine.
    now = int(time.time())
    period1 = now - (30 * 24 * 60 * 60)
    df = _yahoo_chart(stock["symbol"], "5m", period1, now)
    return df


def yahoo_daily_history(symbol):
    now = int(time.time())
    period1 = now - (450 * 24 * 60 * 60)
    return _yahoo_chart(symbol, "1d", period1, now)


_NIFTY_TREND_CACHE = {"day": None, "by_date": None}


def _nifty_daily_raw():
    """NIFTY 50 daily candles from Yahoo (^NSEI); _yahoo_chart would append .NS."""
    now = int(time.time())
    url = YAHOO_BASE_URL + quote(NIFTY_YAHOO_TICKER, safe="")
    params = {"interval": "1d", "period1": now - 200 * 86400, "period2": now,
              "events": "history", "includePrePost": "false"}
    response = requests.get(url, params=params, headers=YAHOO_HEADERS, timeout=20)
    response.raise_for_status()
    r = ((response.json().get("chart") or {}).get("result") or [None])[0]
    if not r:
        raise RuntimeError("Yahoo returned no NIFTY daily data")
    closes = ((r.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    rows = []
    for ts, c in zip(r.get("timestamp") or [], closes):
        if c is not None:
            rows.append((pd.to_datetime(int(ts), unit="s", utc=True).tz_convert(IST).date(), float(c)))
    return rows


def get_nifty_trend_by_date():
    """{date: True/False} -> NIFTY daily trend UP on that date, using only the
    PREVIOUS completed day's close vs its EMA20 (no look-ahead). Refreshed once a day.
    Returns None if the data cannot be loaded."""
    today = datetime.now(IST).date()
    if _NIFTY_TREND_CACHE["day"] == today:
        return _NIFTY_TREND_CACHE["by_date"]
    by_date = None
    try:
        rows = sorted({d: c for d, c in _nifty_daily_raw() if d < today}.items())
        if len(rows) >= NIFTY_DAILY_EMA:
            s = pd.Series([c for _, c in rows], index=[d for d, _ in rows])
            up = (s > s.ewm(span=NIFTY_DAILY_EMA, adjust=False).mean())
            dates = list(up.index)
            by_date = {}
            for k in range(1, len(dates)):
                by_date[dates[k]] = bool(up.iloc[k - 1])
            by_date[today] = bool(up.iloc[-1])
            print(f"NIFTY daily trend today: {'UP - LONG allowed' if by_date[today] else 'NOT UP - LONG blocked'}")
    except Exception as e:
        print(f"NIFTY trend ERROR: {e} -> LONG signals blocked until it loads")
    _NIFTY_TREND_CACHE["day"] = today if by_date is not None else None
    _NIFTY_TREND_CACHE["by_date"] = by_date
    return by_date


def _current_5m_bucket(ts=None):
    if ts is None:
        ts = datetime.now(IST)
    return ts.astimezone(IST).replace(
        minute=(ts.minute // 5) * 5, second=0, microsecond=0
    )


def _append_closed_live_candle(token, state):
    stock = WATCHLIST_BY_TOKEN.get(str(token))
    if stock is None:
        return
    symbol = stock["symbol"]
    candle = {
        "time": pd.Timestamp(state["bucket"]).tz_convert("UTC"),
        "open": float(state["open"]),
        "high": float(state["high"]),
        "low": float(state["low"]),
        "close": float(state["close"]),
        "volume": max(0.0, float(state.get("volume", 0.0))),
    }
    with LIVE_LOCK:
        df = LOCAL_5M_CACHE.get(symbol)
        if df is None:
            return
        row_df = pd.DataFrame([candle])
        df = pd.concat([df, row_df], ignore_index=True)
        df = df.sort_values("time").drop_duplicates("time", keep="last").tail(CANDLE_LIMIT_5M).reset_index(drop=True)
        LOCAL_5M_CACHE[symbol] = df


def _on_live_tick(wsapp, message):
    try:
        if not isinstance(message, dict):
            return
        token = str(message.get("token", ""))
        stock = WATCHLIST_BY_TOKEN.get(token)
        if stock is None:
            return
        raw_price = message.get("last_traded_price")
        if raw_price is None:
            return
        price = float(raw_price) / 100.0
        if price <= 0:
            return
        ts_ms = message.get("exchange_timestamp")
        ts = (pd.to_datetime(int(ts_ms), unit="ms", utc=True).tz_convert(IST)
              if ts_ms else datetime.now(IST))
        bucket = _current_5m_bucket(ts)
        day_volume = message.get("volume_trade_for_the_day")
        day_volume = float(day_volume) if day_volume is not None else None

        with LIVE_LOCK:
            state = LIVE_CANDLE_STATE.get(token)
            if state is None or state["bucket"] != bucket:
                if state is not None:
                    _append_closed_live_candle(token, state)
                baseline = day_volume if day_volume is not None else 0.0
                LIVE_CANDLE_STATE[token] = {
                    "bucket": bucket, "open": price, "high": price,
                    "low": price, "close": price,
                    "start_day_volume": baseline,
                    "volume": 0.0,
                }
                state = LIVE_CANDLE_STATE[token]
            else:
                state["high"] = max(state["high"], price)
                state["low"] = min(state["low"], price)
                state["close"] = price

            if day_volume is not None:
                start = float(state.get("start_day_volume", day_volume))
                state["volume"] = max(0.0, day_volume - start)
            else:
                qty = float(message.get("last_traded_quantity") or 0.0)
                state["volume"] = float(state.get("volume", 0.0)) + max(0.0, qty)
    except Exception as e:
        print(f"WebSocket tick error: {e}")


def start_market_websocket(stocks, client):
    global WEBSOCKET_INSTANCE, WEBSOCKET_THREAD, WEBSOCKET_STARTED, WATCHLIST_BY_TOKEN
    if WEBSOCKET_STARTED:
        return
    try:
        from SmartApi.smartWebSocketV2 import SmartWebSocketV2
    except Exception as e:
        raise RuntimeError(f"SmartWebSocketV2 unavailable: {e}")

    WATCHLIST_BY_TOKEN = {str(x["token"]): x for x in stocks}
    auth_token = get_auth_token()
    feed_token = get_feed_token()
    from angel_client import API_KEY, CLIENT_ID  # invisible marks already removed
    api_key = API_KEY or ""
    client_code = CLIENT_ID or ""
    if not all([auth_token, feed_token, api_key, client_code]):
        raise RuntimeError("Angel WebSocket credentials/session data are incomplete")

    WEBSOCKET_INSTANCE = SmartWebSocketV2(
        auth_token, api_key, client_code, feed_token,
        max_retry_attempt=3, retry_strategy=1, retry_delay=5, retry_multiplier=2, retry_duration=60
    )

    tokens = [str(x["token"]) for x in stocks]
    token_list = [{"exchangeType": 1, "tokens": tokens}]

    def on_open(wsapp):
        print(f"WEBSOCKET: CONNECTED | subscribing {len(tokens)} NSE stocks | mode=3 SNAP_QUOTE")
        WEBSOCKET_INSTANCE.subscribe("nse5m01", 3, token_list)
        print("WEBSOCKET: SUBSCRIPTION SENT")

    def on_error(wsapp, error):
        print(f"WEBSOCKET ERROR: {error}")

    def on_close(wsapp):
        print("WEBSOCKET: CLOSED")

    def on_control(wsapp, message):
        print(f"WEBSOCKET CONTROL: {message}")

    WEBSOCKET_INSTANCE.on_open = on_open
    WEBSOCKET_INSTANCE.on_data = _on_live_tick
    WEBSOCKET_INSTANCE.on_error = on_error
    WEBSOCKET_INSTANCE.on_close = on_close
    WEBSOCKET_INSTANCE.on_control_message = on_control

    WEBSOCKET_STARTED = True
    WEBSOCKET_THREAD = threading.Thread(
        target=WEBSOCKET_INSTANCE.connect, name="AngelWebSocket", daemon=True
    )
    WEBSOCKET_THREAD.start()
    print("WEBSOCKET: background thread started")


def get_local_5m(stock):
    symbol = stock["symbol"]
    now_bucket = pd.Timestamp(_current_5m_bucket()).tz_convert("UTC")

    with LIVE_LOCK:
        cached = LOCAL_5M_CACHE.get(symbol)
        if cached is None or len(cached) < 50:
            cached = None

    if cached is None:
        if not QUIET_STOCK_LOGS:
            print(f"WARM-UP: downloading Yahoo 5M history for {symbol} (Angel Historical API OFF)")
        df = yahoo_5m_warmup(stock)
        # Do not treat Yahoo's current/open candle as a closed signal candle.
        df = df[df["time"] < now_bucket].tail(CANDLE_LIMIT_5M).reset_index(drop=True)
        with LIVE_LOCK:
            LOCAL_5M_CACHE[symbol] = df
            cached = df

    # Expose the currently-forming local candle as the final row. The V1
    # engine deliberately ignores the final/open candle and evaluates only
    # the previous closed 5M candle.
    with LIVE_LOCK:
        token = str(stock["token"])
        state = LIVE_CANDLE_STATE.get(token)
        df = LOCAL_5M_CACHE[symbol].copy()
        if state is not None and state["bucket"] == now_bucket.tz_convert(IST):
            open_row = pd.DataFrame([{
                "time": now_bucket,
                "open": float(state["open"]),
                "high": float(state["high"]),
                "low": float(state["low"]),
                "close": float(state["close"]),
                "volume": float(state.get("volume", 0.0)),
            }])
        else:
            # Before the first live tick, keep a synthetic open candle based
            # on the latest closed price. It is ignored by the V1 engine but
            # preserves the closed-candle indexing contract.
            last_close = float(df.iloc[-1]["close"])
            open_row = pd.DataFrame([{
                "time": now_bucket,
                "open": last_close,
                "high": last_close,
                "low": last_close,
                "close": last_close,
                "volume": 0.0,
            }])
        df = pd.concat([df[df["time"] < now_bucket], open_row], ignore_index=True)
        return df.tail(CANDLE_LIMIT_5M).reset_index(drop=True)


# ============================================================
# DAILY CANDLES / INDICATORS
# ============================================================
def fetch_daily_candles(client, token_or_symbol):
    # Daily MACD warm-up also bypasses Angel Historical API.
    symbol = token_or_symbol if isinstance(token_or_symbol, str) and not token_or_symbol.isdigit() else str(token_or_symbol)
    return yahoo_daily_history(symbol)


def add_daily_indicators(df):
    d = df.copy()
    fast = d["close"].ewm(span=DAILY_MACD_FAST, adjust=False).mean()
    slow = d["close"].ewm(span=DAILY_MACD_SLOW, adjust=False).mean()
    d["macd"] = fast - slow
    d["macd_signal"] = d["macd"].ewm(span=DAILY_MACD_SIGNAL, adjust=False).mean()
    return d


def rsi_wilder(series, length=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.fillna(100).astype(float)
    return rsi

# ============================================================
# INDICATORS
# ============================================================
def add_indicators(df):
    d = df.copy()

    # Yahoo/pandas can occasionally return nullable pd.NA values for a
    # particular NSE symbol. Convert OHLCV to real numeric dtype before
    # any float()/indicator operations so one bad value cannot kill a stock.
    for col in ["open", "high", "low", "close", "volume"]:
        if col in d.columns:
            d[col] = pd.to_numeric(d[col], errors="coerce")

    # A candle with incomplete OHLC cannot participate in the SMC state
    # machine. Drop only those bad rows; preserve valid history.
    d = d.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
    d["volume"] = d["volume"].fillna(0.0).astype(float)

    previous_close = d["close"].shift(1)

    tr = pd.concat(
        [
            d["high"] - d["low"],
            (d["high"] - previous_close).abs(),
            (d["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    # TradingView ta.atr() = Wilder RMA.
    d["atr"] = tr.ewm(
        alpha=1.0 / ATR_LENGTH,
        adjust=False,
        min_periods=ATR_LENGTH,
    ).mean()

    d["volume_sma"] = d["volume"].rolling(
        VOLUME_LENGTH
    ).mean()

    # Session VWAP resets at each Indian trading day.
    ist_dates = d["time"].apply(lambda x: ist_time(x).date())
    typical_price = (d["high"] + d["low"] + d["close"]) / 3.0
    pv = typical_price * d["volume"].fillna(0.0)
    vv = d["volume"].fillna(0.0)
    vwap_den = vv.groupby(ist_dates).cumsum()
    d["vwap"] = pv.groupby(ist_dates).cumsum().div(vwap_den)
    # If a symbol has zero cumulative volume, VWAP is unavailable. Use
    # typical price as a safe numeric fallback so the scanner continues;
    # the VWAP filter remains conservative because price == VWAP will not
    # satisfy the strict > / < entry tests.
    d["vwap"] = pd.to_numeric(d["vwap"], errors="coerce")
    d["vwap"] = d["vwap"].fillna(typical_price).astype(float)

    d["rsi"] = rsi_wilder(d["close"], RSI_LENGTH)

    d["body"] = (
        d["close"] - d["open"]
    ).abs()

    d["ema_htf"] = d["close"].ewm(
        span=HIGHER_TF_EMA_LENGTH,
        adjust=False,
    ).mean()

    return d


# ============================================================
# CONFIRMED PIVOTS
# ============================================================
def calculate_confirmed_pivots(df):
    d = df.copy()

    d["pivot_high"] = False
    d["pivot_low"] = False

    for i in range(
        SWING_LEFT,
        len(d) - SWING_RIGHT,
    ):
        center_high = d.iloc[i]["high"]
        left_high = d.iloc[
            i - SWING_LEFT:i
        ]["high"].max()
        right_high = d.iloc[
            i + 1:i + SWING_RIGHT + 1
        ]["high"].max()

        center_low = d.iloc[i]["low"]
        left_low = d.iloc[
            i - SWING_LEFT:i
        ]["low"].min()
        right_low = d.iloc[
            i + 1:i + SWING_RIGHT + 1
        ]["low"].min()

        if (
            center_high > left_high
            and center_high > right_high
        ):
            confirm_index = i + SWING_RIGHT
            d.at[
                d.index[confirm_index],
                "pivot_high",
            ] = True

        if (
            center_low < left_low
            and center_low < right_low
        ):
            confirm_index = i + SWING_RIGHT
            d.at[
                d.index[confirm_index],
                "pivot_low",
            ] = True

    return d


# ============================================================
# TIME DISPLAY ONLY
# ============================================================
def ist_time(timestamp):
    return timestamp.to_pydatetime().astimezone(IST)


# 5M CANDLE CACHE
# Reuse downloaded 5M history until the next 15-minute bucket.
INTRADAY_CACHE = {}

def get_cached_5m(client, stock):
    # Live candle source: Angel WebSocket.
    # Historical warm-up: Yahoo. Never call Angel getCandleData here.
    return get_local_5m(stock)


# ============================================================
# CORE TRADINGVIEW V1 STATE MACHINE
# ============================================================
def scan_stock(client, stock, daily_cache):
    symbol = stock["symbol"]
    token = stock["token"]

    raw = get_cached_5m(client, stock)

    d = add_indicators(raw)
    d = calculate_confirmed_pivots(d)

    # Daily MACD is deliberately fetched LAZILY.
    # Most stocks never reach the final ENTRY state, so downloading 450 days
    # for every stock would waste historical-API requests.
    daily_macd = float("nan")
    daily_macd_signal = float("nan")
    daily_macd_long = False
    daily_macd_short = False

    # Ignore current/open candle.
    last_closed = len(d) - 2

    if last_closed < 50:
        return {
            "symbol": symbol,
            "signal": "NO TRADE",
            "reason": "Insufficient closed candles",
        }

    nifty_trend = get_nifty_trend_by_date() if USE_NIFTY_TREND_LONG else None

    setup_state = 0
    direction = 0
    setup_start_bar = None

    mss_high = None
    mss_low = None

    zone_high = None
    zone_low = None
    zone_type = ""

    previous_swing_high = None
    previous_swing_low = None

    last_result = None
    stage_times = {}
    stage_ohlc = {}

    for i in range(last_closed + 1):
        row = d.iloc[i]

        # ----------------------------------------------------
        # Update confirmed pivots
        # ----------------------------------------------------
        if bool(row["pivot_high"]):
            pivot_index = i - SWING_RIGHT
            previous_swing_high = float(
                d.iloc[pivot_index]["high"]
            )

        if bool(row["pivot_low"]):
            pivot_index = i - SWING_RIGHT
            previous_swing_low = float(
                d.iloc[pivot_index]["low"]
            )

        # ----------------------------------------------------
        # New calendar day reset
        # ----------------------------------------------------
        if i > 0:
            prev_day = ist_time(
                d.iloc[i - 1]["time"]
            ).date()
            curr_day = ist_time(
                d.iloc[i]["time"]
            ).date()

            if curr_day != prev_day:
                setup_state = 0
                direction = 0
                setup_start_bar = None
                mss_high = None
                mss_low = None
                zone_high = None
                zone_low = None
                zone_type = ""

        atr_value = row["atr"]
        volume_sma = row["volume_sma"]

        if pd.isna(atr_value):
            continue

        body = abs(
            float(row["close"])
            - float(row["open"])
        )

        bullish_displacement = (
            row["close"] > row["open"]
            and body >= atr_value * MINIMUM_DISPLACEMENT_ATR
        )

        bearish_displacement = (
            row["close"] < row["open"]
            and body >= atr_value * MINIMUM_DISPLACEMENT_ATR
        )

        volume_pass = (
            not USE_VOLUME_FILTER
            or (
                not pd.isna(volume_sma)
                and row["volume"] >= volume_sma
            )
        )

        # ----------------------------------------------------
        # Liquidity sweep
        # ----------------------------------------------------
        bull_sweep = (
            previous_swing_low is not None
            and row["low"] < previous_swing_low
            and row["close"] > previous_swing_low
        )

        bear_sweep = (
            previous_swing_high is not None
            and row["high"] > previous_swing_high
            and row["close"] < previous_swing_high
        )

        # ----------------------------------------------------
        # STEP 1 - SWEEP
        # NO TIME RESTRICTION
        # ----------------------------------------------------
        if setup_state == 0:
            if bull_sweep:
                setup_state = 1
                direction = 1
                setup_start_bar = i
                stage_times["SWEEP"] = ist_time(row["time"]).strftime("%Y-%m-%d %H:%M:%S IST")
                stage_ohlc["SWEEP"] = (float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]))
                mss_high = previous_swing_high
                mss_low = None
                zone_high = None
                zone_low = None
                zone_type = ""

            elif bear_sweep:
                setup_state = 1
                direction = -1
                setup_start_bar = i
                stage_times["SWEEP"] = ist_time(row["time"]).strftime("%Y-%m-%d %H:%M:%S IST")
                stage_ohlc["SWEEP"] = (float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]))
                mss_low = previous_swing_low
                mss_high = None
                zone_high = None
                zone_low = None
                zone_type = ""

        # ----------------------------------------------------
        # SETUP EXPIRY
        # This is NOT a clock-time restriction.
        # ----------------------------------------------------
        if (
            setup_state > 0
            and setup_state < 7
            and setup_start_bar is not None
            and i - setup_start_bar > SETUP_EXPIRY_BARS
        ):
            setup_state = 0
            direction = 0
            setup_start_bar = None
            mss_high = None
            mss_low = None
            zone_high = None
            zone_low = None
            zone_type = ""

        # ----------------------------------------------------
        # STEP 2 - DISPLACEMENT
        # ----------------------------------------------------
        if setup_state == 1:
            if direction == 1 and bullish_displacement:
                setup_state = 2
                stage_times["DISPLACEMENT"] = ist_time(row["time"]).strftime("%Y-%m-%d %H:%M:%S IST")
                stage_ohlc["DISPLACEMENT"] = (float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]))
            elif direction == -1 and bearish_displacement:
                setup_state = 2
                stage_times["DISPLACEMENT"] = ist_time(row["time"]).strftime("%Y-%m-%d %H:%M:%S IST")
                stage_ohlc["DISPLACEMENT"] = (float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]))

        # ----------------------------------------------------
        # STEP 3 - MSS
        # ----------------------------------------------------
        if setup_state == 2:
            if (
                direction == 1
                and mss_high is not None
                and row["close"] > mss_high
            ):
                setup_state = 3
                stage_times["MSS"] = ist_time(row["time"]).strftime("%Y-%m-%d %H:%M:%S IST")
                stage_ohlc["MSS"] = (float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]))

            elif (
                direction == -1
                and mss_low is not None
                and row["close"] < mss_low
            ):
                setup_state = 3
                stage_times["MSS"] = ist_time(row["time"]).strftime("%Y-%m-%d %H:%M:%S IST")
                stage_ohlc["MSS"] = (float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]))

        # ----------------------------------------------------
        # STEP 4 - FVG
        # ----------------------------------------------------
        bullish_fvg = False
        bearish_fvg = False

        if i >= 2:
            bullish_fvg = (
                row["low"] > d.iloc[i - 2]["high"]
            )
            bearish_fvg = (
                row["high"] < d.iloc[i - 2]["low"]
            )

        if setup_state == 3 and USE_FVG:
            if direction == 1 and bullish_fvg:
                zone_low = float(
                    d.iloc[i - 2]["high"]
                )
                zone_high = float(row["low"])
                zone_type = "BULL FVG"
                setup_state = 4
                setup_start_bar = i
                stage_times["FVG"] = ist_time(row["time"]).strftime("%Y-%m-%d %H:%M:%S IST")
                stage_ohlc["FVG"] = (float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]))

            elif direction == -1 and bearish_fvg:
                zone_low = float(row["high"])
                zone_high = float(
                    d.iloc[i - 2]["low"]
                )
                zone_type = "BEAR FVG"
                setup_state = 4
                setup_start_bar = i
                stage_times["FVG"] = ist_time(row["time"]).strftime("%Y-%m-%d %H:%M:%S IST")
                stage_ohlc["FVG"] = (float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]))

        # ----------------------------------------------------
        # STEP 4B - ORDER BLOCK
        # ----------------------------------------------------
        if setup_state == 3 and USE_OB:
            if direction == 1:
                for lookback in range(1, OB_LOOKBACK + 1):
                    if i - lookback < 0:
                        break

                    candle = d.iloc[i - lookback]

                    if candle["close"] < candle["open"]:
                        zone_high = float(candle["high"])
                        zone_low = float(candle["low"])
                        zone_type = "BULL OB"
                        setup_state = 4
                        setup_start_bar = i
                        stage_times["OB"] = ist_time(row["time"]).strftime("%Y-%m-%d %H:%M:%S IST")
                        stage_ohlc["OB"] = (float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]))
                        break

            elif direction == -1:
                for lookback in range(1, OB_LOOKBACK + 1):
                    if i - lookback < 0:
                        break

                    candle = d.iloc[i - lookback]

                    if candle["close"] > candle["open"]:
                        zone_high = float(candle["high"])
                        zone_low = float(candle["low"])
                        zone_type = "BEAR OB"
                        setup_state = 4
                        setup_start_bar = i
                        stage_times["OB"] = ist_time(row["time"]).strftime("%Y-%m-%d %H:%M:%S IST")
                        stage_ohlc["OB"] = (float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]))
                        break

        # ----------------------------------------------------
        # STEP 5 - RETEST
        # ----------------------------------------------------
        zone_touched = (
            zone_high is not None
            and zone_low is not None
            and row["high"] >= zone_low
            and row["low"] <= zone_high
        )

        if (
            setup_state == 4
            and setup_start_bar is not None
            and i > setup_start_bar
            and zone_touched
        ):
            setup_state = 5
            stage_times["RETEST"] = ist_time(row["time"]).strftime("%Y-%m-%d %H:%M:%S IST")
            stage_ohlc["RETEST"] = (float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]))

        # ----------------------------------------------------
        # STEP 6 - CONFIRMATION
        # ----------------------------------------------------
        bull_confirmation = False
        bear_confirmation = False

        if i >= 1:
            bull_confirmation = (
                row["close"] > row["open"]
                and row["close"] > d.iloc[i - 1]["high"]
            )

            bear_confirmation = (
                row["close"] < row["open"]
                and row["close"] < d.iloc[i - 1]["low"]
            )

        if setup_state == 5:
            if direction == 1 and bull_confirmation:
                setup_state = 6
                stage_times["CONFIRMATION"] = ist_time(row["time"]).strftime("%Y-%m-%d %H:%M:%S IST")
                stage_ohlc["CONFIRMATION"] = (float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]))
            elif direction == -1 and bear_confirmation:
                setup_state = 6
                stage_times["CONFIRMATION"] = ist_time(row["time"]).strftime("%Y-%m-%d %H:%M:%S IST")
                stage_ohlc["CONFIRMATION"] = (float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]))

        # ----------------------------------------------------
        # ENTRY
        # NO TIME RESTRICTION
        # ----------------------------------------------------
        htf_status = "OFF"
        htf_pass = True

        # Keep V1 default HTF filter OFF.
        # If enabled later, fetch separately.

        rsi_value = float(row["rsi"]) if not pd.isna(row["rsi"]) else float("nan")
        rsi_pass = (
            not USE_RSI_FILTER
            or (direction == 1 and rsi_value > RSI_LONG_MIN)
            or (direction == -1 and rsi_value < RSI_SHORT_MAX)
        )

        vwap_value = float(row["vwap"]) if not pd.isna(row["vwap"]) else float("nan")
        vwap_pass = (
            not USE_VWAP_FILTER
            or (not math.isnan(vwap_value) and (
                (direction == 1 and row["close"] > vwap_value)
                or (direction == -1 and row["close"] < vwap_value)
            ))
        )

        # Daily MACD is checked only after the SMC setup reaches confirmation.
        # This cuts historical requests dramatically.
        macd_pass = True
        if setup_state == 6 and USE_DAILY_MACD_FILTER:
            cache_key = stock["symbol"]
            if cache_key not in daily_cache:
                daily_raw = fetch_daily_candles(client, symbol)
                daily_cache[cache_key] = add_daily_indicators(daily_raw)

            daily = daily_cache[cache_key]
            if len(daily) < 40:
                macd_pass = False
            else:
                daily_last = daily.iloc[-1]
                daily_macd = float(daily_last["macd"])
                daily_macd_signal = float(daily_last["macd_signal"])
                daily_macd_long = daily_macd > daily_macd_signal
                daily_macd_short = daily_macd < daily_macd_signal

                macd_pass = (
                    (direction == 1 and daily_macd_long)
                    or (direction == -1 and daily_macd_short)
                )

        # FINAL CANDIDATE ENTRY FILTERS (from the validated 5M backtest):
        # LONG  : entry candle must be BEFORE 12:00 IST, and BULL FVG LONG is blocked.
        # SHORT : entry candle allowed through 14:00 IST.
        entry_ist = ist_time(row["time"])
        entry_minutes = entry_ist.hour * 60 + entry_ist.minute
        entry_start_minutes = ENTRY_START_HOUR * 60 + ENTRY_START_MINUTE
        time_pass = (
            entry_minutes >= entry_start_minutes
            and (
                (direction == 1 and entry_minutes < (12 * 60))
                or (direction == -1 and entry_minutes <= (14 * 60))
            )
        )
        zone_pass = not (direction == 1 and zone_type == "BULL FVG")
        nifty_pass = (
            not USE_NIFTY_TREND_LONG
            or direction != 1
            or (nifty_trend is not None and nifty_trend.get(entry_ist.date(), False))
        )

        entry_allowed = (
            setup_state == 6
            and time_pass
            and zone_pass
            and volume_pass
            and rsi_pass
            and vwap_pass
            and macd_pass
            and htf_pass
            and nifty_pass
        )

        if entry_allowed:
            entry = float(row["close"])

            if direction == 1:
                signal = "LONG"
                sl = entry * (1.0 - STOP_LOSS_PERCENT / 100.0)
                tp = entry * (1.0 + TP2_PERCENT / 100.0)
                sweep_type = "SSL SWEEP"
                mss_level = mss_high
            else:
                signal = "SHORT"
                sl = entry * (1.0 + STOP_LOSS_PERCENT / 100.0)
                tp = entry * (1.0 - TP2_PERCENT / 100.0)
                sweep_type = "BSL SWEEP"
                mss_level = mss_low

            result = {
                "symbol": symbol,
                "signal": signal,
                "candle_time": ist_time(
                    row["time"]
                ).strftime("%Y-%m-%d %H:%M:%S IST"),
                "entry": entry,
                "sl": sl,
                "tp": tp,
                "rr": TP2_PERCENT / STOP_LOSS_PERCENT,
                "liquidity_level": (
                    previous_swing_low
                    if direction == 1
                    else previous_swing_high
                ),
                "sweep_type": sweep_type,
                "displacement_atr": body / atr_value,
                "mss_level": float(mss_level) if mss_level is not None else 0.0,
                "zone_type": zone_type,
                "volume": float(row["volume"]),
                "volume_sma": float(volume_sma),
                "rsi_5m": rsi_value,
                "rsi_condition": "> 50" if direction == 1 else "< 50",
                "vwap": vwap_value,
                "vwap_condition": "PRICE > VWAP" if direction == 1 else "PRICE < VWAP",
                "daily_macd": daily_macd,
                "daily_macd_signal": daily_macd_signal,
                "market_cap_cr": float(stock.get("market_cap_cr") or 0),
                "htf_status": htf_status,
                "reason": "Complete TradingView V1 sequential setup",
                "stage_times": dict(stage_times),
            }

            if i == last_closed:
                last_result = result

            setup_state = 7

    if last_result is not None:
        return last_result

    return {
        "symbol": symbol,
        "signal": "NO TRADE",
        "state": setup_state,
        "direction": (
            "LONG" if direction == 1
            else "SHORT" if direction == -1
            else "WAIT"
        ),
        "reason": f"Waiting at state {setup_state}",
        "stage_times": dict(stage_times),
    }


# ============================================================
# PRINT
# ============================================================
def print_result(result):
    if QUIET_STOCK_LOGS and result.get("signal") not in ("LONG", "SHORT"):
        return
    print()
    print("=" * 78)
    print(f" {result['symbol']}")

    if result["signal"] in ("LONG", "SHORT"):
        print(f" SIGNAL        : {result['signal']}")
        print(f" ENTRY         : ₹{result['entry']:.2f}")
        print(f" SL            : ₹{result['sl']:.2f}")
        print(f" TP 2%         : ₹{result['tp']:.2f}")
        print(f" Zone          : {result['zone_type']}")
        print(f" Sweep         : {result['sweep_type']}")
        print(f" Displacement  : {result['displacement_atr']:.2f} ATR")
        print(f" Volume        : {result['volume']:.0f} / {result['volume_sma']:.0f}")
        print(f" RSI(14)       : {result['rsi_5m']:.2f}")
        print(" Daily MACD    : OFF (not used as entry filter)")
        print(" Watchlist      : USER STOCK NAME LIST")
        print(f" HTF           : {result['htf_status']}")
        print(f" Candle Time   : {result['candle_time']}")
    else:
        print(f" SIGNAL        : {result['signal']}")
        print(f" STATUS        : {result.get('direction', 'WAIT')}")
        print(f" REASON        : {result.get('reason', '')}")

    print("=" * 78)


# ============================================================
# MAIN
# ============================================================
def is_scan_window(now=None):
    """Indian NSE scan window: weekdays, 09:15 through 15:10 IST."""
    now = now or datetime.now(IST)

    if now.weekday() >= 5:
        return False

    start = now.replace(
        hour=SCAN_START_HOUR,
        minute=SCAN_START_MINUTE,
        second=0,
        microsecond=0,
    )
    end = now.replace(
        hour=SCAN_END_HOUR,
        minute=SCAN_END_MINUTE,
        second=0,
        microsecond=0,
    )
    return start <= now < end


def seconds_until_next_start(now=None):
    """Seconds until the next NSE scan window starts."""
    now = now or datetime.now(IST)
    start = now.replace(
        hour=SCAN_START_HOUR,
        minute=SCAN_START_MINUTE,
        second=0,
        microsecond=0,
    )

    if now < start and now.weekday() < 5:
        return max(1, int((start - now).total_seconds()))

    days_ahead = 1
    while (now + timedelta(days=days_ahead)).weekday() >= 5:
        days_ahead += 1

    next_start = (now + timedelta(days=days_ahead)).replace(
        hour=SCAN_START_HOUR,
        minute=SCAN_START_MINUTE,
        second=0,
        microsecond=0,
    )
    return max(1, int((next_start - now).total_seconds()))


def seconds_until_next_5m_scan(now=None):
    """Wait until the next 15-minute candle boundary."""
    now = now or datetime.now(IST)
    minute = ((now.minute // SCAN_INTERVAL_MINUTES) + 1) * SCAN_INTERVAL_MINUTES

    if minute >= 60:
        next_time = (now + timedelta(hours=1)).replace(
            minute=0, second=5, microsecond=0
        )
    else:
        next_time = now.replace(
            minute=minute, second=5, microsecond=0
        )

    return max(1, int((next_time - now).total_seconds()))


def send_morning_welcome(stock_count):
    today = datetime.now(IST).strftime("%d-%m-%Y")
    message = (
        "🌅 <b>GOOD MORNING!</b>\n\n"
        "🇮🇳 <b>Indian NSE SMC Scanner</b>\n"
        f"📅 Date: {today}\n"
        "📊 Universe: USER STOCK NAME LIST\n"
        f"📈 Timeframe: 15 Minutes\n"
        f"🔎 Stocks: {stock_count}\n"
        "⏰ Scan Mode: ALL TIME / 24x7\n"
        "📌 RSI: OFF | FVG: OFF (OB only) | Nifty trend for LONG: ON\n"
        "📌 Entry 10:00+ | SL 1.5% | TP 2%\n"
        "🚀 Scanner Status: STARTED\n\n"
        "⚠️ Signal Only — No Automatic Orders"
    )
    return send_telegram_message(message)


_PAPER_BOOK = None


def _paper_book():
    global _PAPER_BOOK
    if _PAPER_BOOK is None:
        _PAPER_BOOK = paper_trade.PaperBook(send=send_telegram_message)
    return _PAPER_BOOK


def paper_open(result, stock):
    try:
        _paper_book().open_trade(result, stock)
    except Exception as e:
        print(f"PAPER TRADE open ERROR: {e}")


def paper_check_exits(stocks):
    """Close virtual trades whose SL / TP / 15:15 exit happened on a closed 5m candle."""
    by_symbol = {s["symbol"]: s for s in stocks}
    now_bucket = pd.Timestamp(_current_5m_bucket()).tz_convert("UTC")

    def candles(trade):
        stock = by_symbol.get(trade["symbol"])
        if stock is None:
            return None
        df = get_local_5m(stock)
        return df[df["time"] < now_bucket]          # closed candles only

    try:
        _paper_book().check_exits(candles)
    except Exception as e:
        print(f"PAPER TRADE exit-check ERROR: {e}")


def main():
    print()
    print("=" * 78)
    print(" INDIAN NSE - TRADINGVIEW V1 SMC WATCHLIST SCANNER")
    print(" Angel One WebSocket + Yahoo warm-up + USER STOCK NAME LIST + Telegram")
    print(" Universe: ONLY stock_names.txt | 5M | RSI + VWAP | Daily MACD OFF | Historical API OFF")
    print(" Liquidity -> Sweep -> Displacement -> MSS -> FVG/OB")
    print(" -> Retest -> Confirmation -> Volume -> RSI -> VWAP -> Time/Zone Filter -> ENTRY")
    print(" FINAL RULES   : ENTRY 10:00+ | LONG <12:00 + NIFTY DAILY UP | SHORT <=14:00 | OB ONLY (FVG OFF) | RSI OFF | VWAP ON")
    print(" RISK          : SL 1.5% | TP 2% | Signal Only")
    print("=" * 78)

    client = None
    stocks = None
    daily_cache = {}
    current_day = None
    welcome_sent_day = None
    scan_number = 0

    while True:
        now = datetime.now(IST)

        if not is_scan_window(now):
            time.sleep(min(60, max(1, seconds_until_next_start(now))))
            continue

        if current_day != now.date():
            current_day = now.date()
            welcome_sent_day = None
            daily_cache = {}
            INTRADAY_CACHE.clear()

            if client is None:
                client = get_client()

            instruments = load_instruments()
            requested_names = load_stock_name_watchlist()
            stocks = build_watchlist_from_names(instruments, requested_names)
            start_market_websocket(stocks, client)

        if welcome_sent_day != current_day:
            send_morning_welcome(len(stocks))
            welcome_sent_day = current_day

        scan_number += 1
        print("\n" + "=" * 78)
        print(f" NSE WATCHLIST SCAN #{scan_number} | {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')}")
        print(f" Stocks from stock_names.txt: {len(stocks)}")
        print(" ONLY user-provided Stock Names are being scanned.")
        print("=" * 78)

        signal_count = 0
        for n, stock in enumerate(stocks, 1):
            if not is_scan_window(datetime.now(IST)):
                break
            try:
                if (n == 1 or n % PROGRESS_EVERY == 0 or n == len(stocks)):
                    print(f"[PROGRESS] {n}/{len(stocks)} stocks scanned", flush=True)
                result = scan_stock(client, stock, daily_cache)
                print_result(result)

                if result.get("signal") in ("LONG", "SHORT"):
                    signal_count += 1
                    key = signal_key(result)
                    is_paper = PAPER_TRADE_SHORT and result["signal"] == "SHORT"
                    if candle_closed_before_run_start(result):
                        SENT_SIGNALS.add(key)
                        print("Telegram: SKIPPED - candle closed before this run started (previous run handled it)")
                        if is_paper:
                            # previous run may have died between alert and ledger write
                            paper_open(result, stock)
                    elif key not in SENT_SIGNALS:
                        if send_telegram_message(build_telegram_message(result)):
                            SENT_SIGNALS.add(key)
                            if is_paper:
                                paper_open(result, stock)   # paper book tracks SL/TP/15:15 exit
                            else:
                                register_active_trade(result)
                            print(
                                f"TRADE MONITORING STARTED: "
                                f"{result['symbol']} {result['signal']} "
                                f"SL={STOP_LOSS_PERCENT:g}% TP={TP2_PERCENT:g}%"
                            )
                    else:
                        print("Telegram: DUPLICATE SIGNAL SKIPPED")

                # Check already-open trades while the stock scan continues.
                monitor_active_trades()

                time.sleep(API_DELAY_SECONDS)

            except HistoricalRateLimitError as e:
                print("-" * 78)
                print(f"HISTORICAL API RATE LIMIT: {e}")
                print("Pausing this scan pass. No retry of the blocked request.")
                print("-" * 78)
                break
            except Exception as e:
                print("-" * 78)
                print(f"{stock['symbol']} ERROR: {e}")
                print("-" * 78)

        if PAPER_TRADE_SHORT:
            paper_check_exits(stocks)

        print("\n" + "=" * 78)
        print("NSE WATCHLIST SCAN PASS COMPLETE")
        print(f"Stocks in watchlist : {len(stocks)}")
        print(f"Entry signals       : {signal_count}")
        print("=" * 78)

        # Wait until the NEXT CLOSED 5-minute candle is available.
        # Example: pass finishes at 13:02 -> next scan starts at the next 5-minute boundary.
        # Sleep in short chunks so the process remains responsive.
        now = datetime.now(IST)
        next_minute = ((now.minute // SCAN_INTERVAL_MINUTES) + 1) * SCAN_INTERVAL_MINUTES
        if next_minute >= 60:
            next_run = (now.replace(second=5, microsecond=0, minute=0) + timedelta(hours=1))
        else:
            next_run = now.replace(second=5, microsecond=0, minute=next_minute)

        wait_seconds = max(1.0, (next_run - now).total_seconds())
        print(
            f"NEXT SCAN : {next_run.strftime('%Y-%m-%d %H:%M:%S IST')} "
            f"(waiting {int(wait_seconds)} seconds)"
        )

        while wait_seconds > 0:
            if not is_scan_window(datetime.now(IST)):
                break
            # Live WebSocket keeps updating prices while we wait.
            # Check active trades frequently so TP/SL alerts are separate
            # from the next 5M scanner pass.
            sleep_for = min(2.0, wait_seconds)
            time.sleep(sleep_for)
            monitor_active_trades()
            wait_seconds = (next_run - datetime.now(IST)).total_seconds()


if __name__ == "__main__":
    main()