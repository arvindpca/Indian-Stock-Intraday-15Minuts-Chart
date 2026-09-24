import os
import time
import math
import json
import threading
from pathlib import Path
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import numpy as np
import requests

# ============================================================
# FAST NSE SMC V1 BACKTEST - 12 MONTH
# Universe: stock_names.txt beside this file
# Entry <= 14:45 IST
# TP = +2%, SL = -1%
# Force exit = 15:10 IST
# Entry logic: TradingView V1 SMC sequence from live scanner
# ============================================================

IST = ZoneInfo("Asia/Kolkata")
DHAN_BASE_URL = "https://api.dhan.co/v2"
DHAN_INSTRUMENT_MASTER = "https://images.dhan.co/api-data/api-scrip-master.csv"
DHAN_ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN", "").strip()

# -------------------- BACKTEST SETTINGS ---------------------
ENTRY_CUTOFF = dtime(14, 45)
FORCE_EXIT_TIME = dtime(15, 10)
TP_PERCENT = 2.0
SL_PERCENT = 1.0

LOOKBACK_DAYS_15M = 365
DAILY_LOOKBACK_DAYS = 450
LOOKBACK_DAYS_5M = 365
INTRADAY_CHUNK_DAYS = 90
MAX_WORKERS = 8
REQUEST_TIMEOUT = 30
RETRIES = 4
REQUESTS_PER_SECOND = 4.0

SWING_LEFT = 2
SWING_RIGHT = 2
ATR_LENGTH = 14
MINIMUM_DISPLACEMENT_ATR = 1.0
USE_FVG = True
USE_OB = True
OB_LOOKBACK = 5
USE_VOLUME_FILTER = True
VOLUME_LENGTH = 20
USE_RSI_FILTER = True
RSI_LENGTH = 14
RSI_LONG_MIN = 50.0
RSI_SHORT_MAX = 50.0
USE_DAILY_MACD_FILTER = True
DAILY_MACD_FAST = 12
DAILY_MACD_SLOW = 26
DAILY_MACD_SIGNAL = 9
SETUP_EXPIRY_BARS = 12

BASE_DIR = Path(__file__).resolve().parent
WATCHLIST_FILE = BASE_DIR / "stock_names.txt"
CACHE_DIR = BASE_DIR / "backtest_cache"
RESULT_DIR = BASE_DIR / "backtest_results"
CACHE_DIR.mkdir(exist_ok=True)
RESULT_DIR.mkdir(exist_ok=True)

PRINT_LOCK = threading.Lock()


def log(msg):
    with PRINT_LOCK:
        print(msg, flush=True)


def clean_symbol(s):
    s = str(s).strip().upper()
    if not s:
        return ""
    s = s.replace("-EQ", "")
    if s.endswith(".NS"):
        s = s[:-3]
    return s


def load_watchlist():
    if not WATCHLIST_FILE.exists():
        raise FileNotFoundError(
            f"{WATCHLIST_FILE} not found. Put stock_names.txt beside this backtest file."
        )
    symbols = []
    seen = set()
    with open(WATCHLIST_FILE, "r", encoding="utf-8-sig") as f:
        for line in f:
            s = clean_symbol(line)
            if s and s not in seen:
                seen.add(s)
                symbols.append(s)
    if not symbols:
        raise RuntimeError("stock_names.txt is empty.")
    return symbols


_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST_TS = 0.0


def dhan_request(path, payload):
    global _LAST_REQUEST_TS
    if not DHAN_ACCESS_TOKEN:
        raise RuntimeError("DHAN_ACCESS_TOKEN is missing. Add it to Railway Variables / .env.")

    url = DHAN_BASE_URL + path
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "access-token": DHAN_ACCESS_TOKEN,
    }
    last_error = None
    for attempt in range(1, RETRIES + 1):
        try:
            with _REQUEST_LOCK:
                wait = (1.0 / REQUESTS_PER_SECOND) - (time.time() - _LAST_REQUEST_TS)
                if wait > 0:
                    time.sleep(wait)
                _LAST_REQUEST_TS = time.time()

            r = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
            if r.status_code != 200:
                try:
                    detail = r.json()
                except Exception:
                    detail = r.text[:500]
                raise RuntimeError(f"Dhan HTTP {r.status_code}: {detail}")
            data = r.json()
            if not isinstance(data, dict) or "timestamp" not in data:
                raise RuntimeError(f"Dhan returned no candle data: {data}")
            return data
        except Exception as e:
            last_error = e
            if attempt < RETRIES:
                time.sleep(min(2.0 * attempt, 8.0))
    raise RuntimeError(str(last_error))


def load_dhan_instrument_map():
    cache_file = CACHE_DIR / "dhan_scrip_master.csv"
    if cache_file.exists():
        try:
            return pd.read_csv(cache_file, low_memory=False)
        except Exception:
            pass

    r = requests.get(DHAN_INSTRUMENT_MASTER, timeout=60)
    r.raise_for_status()
    cache_file.write_bytes(r.content)
    return pd.read_csv(cache_file, low_memory=False)


def build_security_map(symbols):
    master = load_dhan_instrument_map()
    # Dhan compact master uses SEM_* names. Prefer NSE cash equity rows.
    required = {"SEM_EXM_EXCH_ID", "SEM_SEGMENT", "SEM_SYMBOL_NAME", "SEM_SMST_SECURITY_ID"}
    missing = required - set(master.columns)
    if missing:
        raise RuntimeError(f"Dhan scrip master missing columns: {sorted(missing)}")

    m = master[
        (master["SEM_EXM_EXCH_ID"].astype(str).str.upper() == "NSE")
        & (master["SEM_SEGMENT"].astype(str).str.upper() == "E")
    ].copy()
    m["symbol_clean"] = m["SEM_SYMBOL_NAME"].astype(str).str.upper().str.strip()
    m = m.drop_duplicates("symbol_clean", keep="first")

    result = {}
    for s in symbols:
        row = m[m["symbol_clean"] == clean_symbol(s)]
        if row.empty:
            continue
        result[clean_symbol(s)] = str(row.iloc[0]["SEM_SMST_SECURITY_ID"])
    return result


def _dhan_to_df(payload):
    timestamps = payload.get("timestamp") or []
    opens = payload.get("open") or []
    highs = payload.get("high") or []
    lows = payload.get("low") or []
    closes = payload.get("close") or []
    volumes = payload.get("volume") or []

    n = min(len(timestamps), len(opens), len(highs), len(lows), len(closes), len(volumes))
    rows = []
    for i in range(n):
        try:
            if any(x is None for x in (timestamps[i], opens[i], highs[i], lows[i], closes[i], volumes[i])):
                continue
            rows.append({
                "time": pd.to_datetime(int(timestamps[i]), unit="s", utc=True),
                "open": float(opens[i]),
                "high": float(highs[i]),
                "low": float(lows[i]),
                "close": float(closes[i]),
                "volume": float(volumes[i]),
            })
        except (TypeError, ValueError, OverflowError):
            continue
    if not rows:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    return pd.DataFrame(rows).sort_values("time").drop_duplicates("time").reset_index(drop=True)


def dhan_candles(security_id, interval, start_dt, end_dt):
    if interval == "1d":
        payload = {
            "securityId": str(security_id),
            "exchangeSegment": "NSE_EQ",
            "instrument": "EQUITY",
            "expiryCode": 0,
            "oi": False,
            "fromDate": start_dt.strftime("%Y-%m-%d"),
            "toDate": end_dt.strftime("%Y-%m-%d"),
        }
        return _dhan_to_df(dhan_request("/charts/historical", payload))

    payload = {
        "securityId": str(security_id),
        "exchangeSegment": "NSE_EQ",
        "instrument": "EQUITY",
        "interval": str(interval),
        "oi": False,
        "fromDate": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "toDate": end_dt.strftime("%Y-%m-%d %H:%M:%S"),
    }
    return _dhan_to_df(dhan_request("/charts/intraday", payload))


def cached_chart(symbol, interval, days, cache_tag, security_id):
    cache_file = CACHE_DIR / f"{clean_symbol(symbol)}_{cache_tag}_12m.pkl.gz"
    if cache_file.exists():
        try:
            return pd.read_pickle(cache_file, compression="gzip")
        except Exception:
            try:
                cache_file.unlink()
            except Exception:
                pass

    end_dt = datetime.now(IST).replace(tzinfo=IST)
    start_dt = end_dt - timedelta(days=days)
    chunks = []

    if interval == "1d":
        chunks.append((start_dt, end_dt))
    else:
        cur = start_dt
        while cur < end_dt:
            nxt = min(cur + timedelta(days=INTRADAY_CHUNK_DAYS), end_dt)
            chunks.append((cur, nxt))
            cur = nxt

    for a, b in chunks:
        df = dhan_candles(security_id, interval, a, b)
        if not df.empty:
            chunks_data = df
            if "_frames" not in locals():
                _frames = []
            _frames.append(chunks_data)

    frames = locals().get("_frames", [])
    if not frames:
        raise RuntimeError(f"Dhan returned no {interval} data for {symbol} ({security_id})")
    out = pd.concat(frames, ignore_index=True)
    out = out.sort_values("time").drop_duplicates("time").reset_index(drop=True)

    # Exclude currently forming candle.
    if interval in (5, 15):
        now_utc = pd.Timestamp.now(tz="UTC")
        minutes = 15 if interval == 15 else 5
        bucket_minute = (now_utc.minute // minutes) * minutes
        current_bucket = now_utc.replace(minute=bucket_minute, second=0, microsecond=0)
        out = out[out["time"] < current_bucket]

    out.to_pickle(cache_file, compression="gzip")
    return out.reset_index(drop=True)

def add_indicators(df):
    d = df.copy()
    previous_close = d["close"].shift(1)

    tr = pd.concat(
        [
            d["high"] - d["low"],
            (d["high"] - previous_close).abs(),
            (d["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    d["atr"] = tr.ewm(
        alpha=1.0 / ATR_LENGTH,
        adjust=False,
        min_periods=ATR_LENGTH,
    ).mean()

    d["volume_sma"] = d["volume"].rolling(VOLUME_LENGTH).mean()

    delta = d["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(
        alpha=1.0 / RSI_LENGTH,
        adjust=False,
        min_periods=RSI_LENGTH,
    ).mean()
    avg_loss = loss.ewm(
        alpha=1.0 / RSI_LENGTH,
        adjust=False,
        min_periods=RSI_LENGTH,
    ).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    d["rsi"] = (100 - (100 / (1 + rs))).fillna(100)

    return d


def calculate_confirmed_pivots(df):
    d = df.copy()
    d["pivot_high"] = False
    d["pivot_low"] = False

    for i in range(SWING_LEFT, len(d) - SWING_RIGHT):
        center_high = d.iloc[i]["high"]
        left_high = d.iloc[i - SWING_LEFT:i]["high"].max()
        right_high = d.iloc[i + 1:i + SWING_RIGHT + 1]["high"].max()

        center_low = d.iloc[i]["low"]
        left_low = d.iloc[i - SWING_LEFT:i]["low"].min()
        right_low = d.iloc[i + 1:i + SWING_RIGHT + 1]["low"].min()

        if center_high > left_high and center_high > right_high:
            d.at[d.index[i + SWING_RIGHT], "pivot_high"] = True

        if center_low < left_low and center_low < right_low:
            d.at[d.index[i + SWING_RIGHT], "pivot_low"] = True

    return d


def ist_dt(ts):
    return ts.to_pydatetime().astimezone(IST)


def daily_macd_as_of(daily_df, signal_date):
    """MACD using only completed daily candles available on signal_date."""
    d = daily_df.copy()
    d["ist_date"] = d["time"].map(lambda x: ist_dt(x).date())
    d = d[d["ist_date"] < signal_date].copy()

    if len(d) < 40:
        return None, None

    fast = d["close"].ewm(span=DAILY_MACD_FAST, adjust=False).mean()
    slow = d["close"].ewm(span=DAILY_MACD_SLOW, adjust=False).mean()
    macd = fast - slow
    signal = macd.ewm(span=DAILY_MACD_SIGNAL, adjust=False).mean()

    return float(macd.iloc[-1]), float(signal.iloc[-1])


def detect_entries(symbol, raw15):
    """
    Same V1 state machine as the live scanner, but returns every historical
    entry candidate and applies the 14:45 entry cutoff.
    """
    d = calculate_confirmed_pivots(add_indicators(raw15))
    if len(d) < 60:
        return []

    # Historical data has no "open final row" after filtering, so all rows are
    # usable as closed candles.
    last_closed = len(d) - 1

    setup_state = 0
    direction = 0
    setup_start_bar = None
    mss_high = None
    mss_low = None
    previous_swing_high = None
    previous_swing_low = None
    zone_high = None
    zone_low = None
    zone_type = ""
    stage_times = {}

    candidates = []

    for i in range(last_closed + 1):
        row = d.iloc[i]
        row_dt = ist_dt(row["time"])
        row_date = row_dt.date()

        if bool(row["pivot_high"]):
            pivot_index = i - SWING_RIGHT
            if pivot_index >= 0:
                previous_swing_high = float(d.iloc[pivot_index]["high"])

        if bool(row["pivot_low"]):
            pivot_index = i - SWING_RIGHT
            if pivot_index >= 0:
                previous_swing_low = float(d.iloc[pivot_index]["low"])

        # Initialize on first usable bar.
        if i == 0:
            previous_swing_high = locals().get("previous_swing_high", None)
            previous_swing_low = locals().get("previous_swing_low", None)
        else:
            prev_day = ist_dt(d.iloc[i - 1]["time"]).date()
            if row_date != prev_day:
                setup_state = 0
                direction = 0
                setup_start_bar = None
                mss_high = None
                mss_low = None
                zone_high = None
                zone_low = None
                zone_type = ""
                stage_times = {}

        atr = row["atr"]
        vol_sma = row["volume_sma"]
        if pd.isna(atr):
            continue

        body = abs(float(row["close"]) - float(row["open"]))
        bullish_disp = row["close"] > row["open"] and body >= atr * MINIMUM_DISPLACEMENT_ATR
        bearish_disp = row["close"] < row["open"] and body >= atr * MINIMUM_DISPLACEMENT_ATR

        volume_pass = (
            not USE_VOLUME_FILTER
            or (not pd.isna(vol_sma) and row["volume"] > vol_sma)
        )

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

        if setup_state == 0:
            if bull_sweep:
                setup_state = 1
                direction = 1
                setup_start_bar = i
                stage_times = {
                    "SWEEP": row_dt.strftime("%Y-%m-%d %H:%M:%S IST")
                }
                mss_high = previous_swing_high
                mss_low = None
                zone_high = zone_low = None
                zone_type = ""
            elif bear_sweep:
                setup_state = 1
                direction = -1
                setup_start_bar = i
                stage_times = {
                    "SWEEP": row_dt.strftime("%Y-%m-%d %H:%M:%S IST")
                }
                mss_low = previous_swing_low
                mss_high = None
                zone_high = zone_low = None
                zone_type = ""

        if (
            setup_state > 0
            and setup_state < 7
            and setup_start_bar is not None
            and i - setup_start_bar > SETUP_EXPIRY_BARS
        ):
            setup_state = 0
            direction = 0
            setup_start_bar = None
            mss_high = mss_low = None
            zone_high = zone_low = None
            zone_type = ""
            stage_times = {}
            continue

        if setup_state == 1:
            if direction == 1 and bullish_disp:
                setup_state = 2
                stage_times["DISPLACEMENT"] = row_dt.strftime("%Y-%m-%d %H:%M:%S IST")
            elif direction == -1 and bearish_disp:
                setup_state = 2
                stage_times["DISPLACEMENT"] = row_dt.strftime("%Y-%m-%d %H:%M:%S IST")

        if setup_state == 2:
            if direction == 1 and mss_high is not None and row["close"] > mss_high:
                setup_state = 3
                stage_times["MSS"] = row_dt.strftime("%Y-%m-%d %H:%M:%S IST")
            elif direction == -1 and mss_low is not None and row["close"] < mss_low:
                setup_state = 3
                stage_times["MSS"] = row_dt.strftime("%Y-%m-%d %H:%M:%S IST")

        bullish_fvg = False
        bearish_fvg = False
        if i >= 2:
            bullish_fvg = row["low"] > d.iloc[i - 2]["high"]
            bearish_fvg = row["high"] < d.iloc[i - 2]["low"]

        if setup_state == 3 and USE_FVG:
            if direction == 1 and bullish_fvg:
                zone_low = float(d.iloc[i - 2]["high"])
                zone_high = float(row["low"])
                zone_type = "BULL FVG"
                setup_state = 4
                setup_start_bar = i
                stage_times["FVG"] = row_dt.strftime("%Y-%m-%d %H:%M:%S IST")
            elif direction == -1 and bearish_fvg:
                zone_low = float(row["high"])
                zone_high = float(d.iloc[i - 2]["low"])
                zone_type = "BEAR FVG"
                setup_state = 4
                setup_start_bar = i
                stage_times["FVG"] = row_dt.strftime("%Y-%m-%d %H:%M:%S IST")

        if setup_state == 3 and USE_OB:
            if direction == 1:
                for lb in range(1, OB_LOOKBACK + 1):
                    if i - lb < 0:
                        break
                    candle = d.iloc[i - lb]
                    if candle["close"] < candle["open"]:
                        zone_high = float(candle["high"])
                        zone_low = float(candle["low"])
                        zone_type = "BULL OB"
                        setup_state = 4
                        setup_start_bar = i
                        stage_times["OB"] = row_dt.strftime("%Y-%m-%d %H:%M:%S IST")
                        break
            elif direction == -1:
                for lb in range(1, OB_LOOKBACK + 1):
                    if i - lb < 0:
                        break
                    candle = d.iloc[i - lb]
                    if candle["close"] > candle["open"]:
                        zone_high = float(candle["high"])
                        zone_low = float(candle["low"])
                        zone_type = "BEAR OB"
                        setup_state = 4
                        setup_start_bar = i
                        stage_times["OB"] = row_dt.strftime("%Y-%m-%d %H:%M:%S IST")
                        break

        zone_touched = (
            zone_high is not None and zone_low is not None
            and row["high"] >= zone_low and row["low"] <= zone_high
        )

        if (
            setup_state == 4
            and setup_start_bar is not None
            and i > setup_start_bar
            and zone_touched
        ):
            setup_state = 5
            stage_times["RETEST"] = row_dt.strftime("%Y-%m-%d %H:%M:%S IST")

        if i >= 1:
            bull_conf = row["close"] > row["open"] and row["close"] > d.iloc[i - 1]["high"]
            bear_conf = row["close"] < row["open"] and row["close"] < d.iloc[i - 1]["low"]
        else:
            bull_conf = bear_conf = False

        if setup_state == 5:
            if direction == 1 and bull_conf:
                setup_state = 6
                stage_times["CONFIRMATION"] = row_dt.strftime("%Y-%m-%d %H:%M:%S IST")
            elif direction == -1 and bear_conf:
                setup_state = 6
                stage_times["CONFIRMATION"] = row_dt.strftime("%Y-%m-%d %H:%M:%S IST")

        rsi = float(row["rsi"]) if not pd.isna(row["rsi"]) else float("nan")
        rsi_pass = (
            not USE_RSI_FILTER
            or (direction == 1 and rsi > RSI_LONG_MIN)
            or (direction == -1 and rsi < RSI_SHORT_MAX)
        )

        # ENTRY CUTOFF: signal candle must be <= 14:45 IST.
        if setup_state == 6 and row_dt.time() <= ENTRY_CUTOFF:
            # Daily MACD is applied later, after all 15m candidates are found.
            # This avoids downloading daily data for stocks with no SMC setup.
            if volume_pass and rsi_pass:
                entry = float(row["close"])
                signal = "LONG" if direction == 1 else "SHORT"

                if signal == "LONG":
                    sl = entry * (1 - SL_PERCENT / 100)
                    tp = entry * (1 + TP_PERCENT / 100)
                else:
                    sl = entry * (1 + SL_PERCENT / 100)
                    tp = entry * (1 - TP_PERCENT / 100)

                candidates.append({
                    "symbol": symbol,
                    "direction": signal,
                    "entry_time": row_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "entry_time_ist": row_dt,
                    "entry": entry,
                    "sl": sl,
                    "tp": tp,
                    "zone": zone_type,
                    "rsi": rsi,
                    "volume": float(row["volume"]),
                    "volume_sma": float(vol_sma),
                    "daily_macd": None,
                    "daily_macd_signal": None,
                    "sweep_time": stage_times.get("SWEEP", ""),
                    "displacement_time": stage_times.get("DISPLACEMENT", ""),
                    "mss_time": stage_times.get("MSS", ""),
                    "fvg_time": stage_times.get("FVG", ""),
                    "ob_time": stage_times.get("OB", ""),
                    "retest_time": stage_times.get("RETEST", ""),
                    "confirmation_time": stage_times.get("CONFIRMATION", ""),
                })

                # Preserve source V1 behavior: after entry, setup is closed.
                setup_state = 7

    return candidates


def evaluate_trade_5m(candidate, raw5):
    """
    Use 5-minute candles after entry for TP/SL and exact 15:10 force exit.
    This avoids pretending that a 15-minute OHLC candle tells us the price
    at exactly 15:10.
    """
    entry_dt = candidate["entry_time_ist"]
    day = entry_dt.date()
    direction = candidate["direction"]

    d = raw5.copy()
    d["ist"] = d["time"].map(ist_dt)
    d = d[d["ist"].dt.date == day].copy()
    d = d[d["ist"] > entry_dt].copy()

    # Need candles through the 15:10 timestamp. Yahoo 5m labels are candle start.
    # A candle starting at 15:10 gives us a 15:10 bar and is the closest direct
    # OHLC representation of the requested force-exit timestamp.
    force_dt = datetime.combine(day, FORCE_EXIT_TIME).replace(tzinfo=IST)

    for _, row in d.iterrows():
        bar_start = row["ist"]
        bar_end = bar_start + timedelta(minutes=5)

        # If this is the 15:10 candle, use its OPEN as the force-exit price.
        # We cannot know an exact 15:10 transaction price from OHLC history;
        # the bar open is the objective price at 15:10.
        if bar_start.time() == FORCE_EXIT_TIME:
            # Check TP/SL only before force time; at the exact 15:10 boundary,
            # force exit takes precedence.
            return {
                "exit_time": bar_start.strftime("%Y-%m-%d %H:%M:%S"),
                "exit_price": float(row["open"]),
                "exit_reason": "FORCE_EXIT_15:10",
                "pnl_pct": (
                    (float(row["open"]) / candidate["entry"] - 1) * 100
                    if direction == "LONG"
                    else (1 - float(row["open"]) / candidate["entry"]) * 100
                ),
            }

        # Ignore bars that begin before the entry candle has closed.
        # Entry is at 15m candle close, so next 5m bar is the first monitor bar.
        high = float(row["high"])
        low = float(row["low"])

        if direction == "LONG":
            sl_hit = low <= candidate["sl"]
            tp_hit = high >= candidate["tp"]
            if sl_hit and tp_hit:
                reason = "SL_FIRST_SAME_5M_BAR"
                price = candidate["sl"]
            elif sl_hit:
                reason = "SL"
                price = candidate["sl"]
            elif tp_hit:
                reason = "TP_2%"
                price = candidate["tp"]
            else:
                continue
        else:
            sl_hit = high >= candidate["sl"]
            tp_hit = low <= candidate["tp"]
            if sl_hit and tp_hit:
                reason = "SL_FIRST_SAME_5M_BAR"
                price = candidate["sl"]
            elif sl_hit:
                reason = "SL"
                price = candidate["sl"]
            elif tp_hit:
                reason = "TP_2%"
                price = candidate["tp"]
            else:
                continue

        pnl = (
            (price / candidate["entry"] - 1) * 100
            if direction == "LONG"
            else (1 - price / candidate["entry"]) * 100
        )
        return {
            "exit_time": bar_start.strftime("%Y-%m-%d %H:%M:%S"),
            "exit_price": float(price),
            "exit_reason": reason,
            "pnl_pct": float(pnl),
        }

    return None


def process_symbol(symbol, security_id, index, total):
    try:
        raw15 = cached_chart(symbol, 15, LOOKBACK_DAYS_15M, "15m", security_id)
        candidates = detect_entries(symbol, raw15)

        if not candidates:
            log(f"[{index}/{total}] {symbol}: NO TRADE")
            return [], None

        # Only stocks with SMC candidates need daily MACD data.
        daily = cached_chart(symbol, "1d", DAILY_LOOKBACK_DAYS, "1d", security_id)
        filtered = []
        for c in candidates:
            if USE_DAILY_MACD_FILTER:
                macd, macd_signal = daily_macd_as_of(
                    daily, c["entry_time_ist"].date()
                )
                if macd is None:
                    continue
                direction = c["direction"]
                if not (
                    (direction == "LONG" and macd > macd_signal)
                    or (direction == "SHORT" and macd < macd_signal)
                ):
                    continue
                c["daily_macd"] = macd
                c["daily_macd_signal"] = macd_signal
            filtered.append(c)

        if not filtered:
            log(f"[{index}/{total}] {symbol}: SMC candidate(s), MACD FILTER")
            return [], None

        # Only stocks with final entry candidates need 5m data.
        raw5 = cached_chart(symbol, 5, LOOKBACK_DAYS_5M, "5m", security_id)

        rows = []
        for candidate in filtered:
            result = evaluate_trade_5m(candidate, raw5)
            if result is None:
                continue
            row = dict(candidate)
            row.pop("entry_time_ist", None)
            row.update(result)
            rows.append(row)

        log(f"[{index}/{total}] {symbol}: {len(rows)} trade(s)")
        return rows, None

    except Exception as e:
        log(f"[{index}/{total}] {symbol}: ERROR - {e}")
        return [], {"symbol": symbol, "error": str(e)}


def main():
    symbols = load_watchlist()
    total = len(symbols)

    if not DHAN_ACCESS_TOKEN:
        raise RuntimeError("DHAN_ACCESS_TOKEN is required. Add it in Railway Variables.")

    security_map = build_security_map(symbols)
    missing_symbols = [s for s in symbols if s not in security_map]

    print("=" * 90)
    print("FAST NSE SMC V1 BACKTEST")
    print("=" * 90)
    print(f"Watchlist             : {WATCHLIST_FILE.name}")
    print(f"Stocks                 : {total}")
    print(f"15M history            : {LOOKBACK_DAYS_15M} days")
    print(f"Entry cutoff           : {ENTRY_CUTOFF.strftime('%H:%M')} IST")
    print(f"Target                 : +{TP_PERCENT:.2f}%")
    print(f"Stop loss              : -{SL_PERCENT:.2f}%")
    print(f"Force exit              : {FORCE_EXIT_TIME.strftime('%H:%M')} IST")
    print(f"Workers                : {MAX_WORKERS}")
    print(f"Data source             : DhanHQ Historical API")
    print(f"Mapped securities       : {len(security_map)}/{total}")
    print(f"Cache                  : {CACHE_DIR}")
    print("=" * 90)

    all_trades = []
    errors = []

    started = time.time()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(process_symbol, symbol, security_map[symbol], i, total): symbol
            for i, symbol in enumerate(symbols, 1) if symbol in security_map
        }

        for future in as_completed(futures):
            trades, error = future.result()
            all_trades.extend(trades)
            if error:
                errors.append(error)

    for symbol in missing_symbols:
        errors.append({"symbol": symbol, "error": "No NSE_EQ security ID found in Dhan scrip master"})

    elapsed = time.time() - started

    trades_df = pd.DataFrame(all_trades)
    if trades_df.empty:
        print("\nNO COMPLETED TRADES FOUND.")
        if errors:
            pd.DataFrame(errors).to_csv(
                RESULT_DIR / "backtest_errors.csv", index=False
            )
        return

    trades_df = trades_df.sort_values(
        ["entry_time", "symbol"]
    ).reset_index(drop=True)

    # Sequential cumulative P&L in percentage points.
    trades_df["cum_pnl_pct"] = trades_df["pnl_pct"].cumsum()

    # R multiple based on 1% risk.
    trades_df["R"] = trades_df["pnl_pct"] / SL_PERCENT

    # Save full trade list.
    trade_file = RESULT_DIR / "SMC_Backtest_Trades_12M.csv"
    trades_df.to_csv(trade_file, index=False)

    # Stock-wise summary.
    stock_rows = []
    for symbol, g in trades_df.groupby("symbol", sort=True):
        wins_g = int((g["pnl_pct"] > 0).sum())
        stock_rows.append({
            "symbol": symbol,
            "Trades": len(g),
            "Wins": wins_g,
            "Losses": int((g["pnl_pct"] < 0).sum()),
            "WinRate_%": round(wins_g / len(g) * 100, 2),
            "NetPnL_%": round(float(g["pnl_pct"].sum()), 4),
            "AvgTrade_%": round(float(g["pnl_pct"].mean()), 4),
            "Total_R": round(float(g["R"].sum()), 4),
        })
    stock_df = pd.DataFrame(stock_rows).sort_values(
        "NetPnL_%", ascending=False
    )
    stock_file = RESULT_DIR / "SMC_Backtest_Stock_Summary_12M.csv"
    stock_df.to_csv(stock_file, index=False)

    total_trades = len(trades_df)
    wins = int((trades_df["pnl_pct"] > 0).sum())
    losses = int((trades_df["pnl_pct"] < 0).sum())
    force = int(trades_df["exit_reason"].eq("FORCE_EXIT_15:10").sum())
    tp = int(trades_df["exit_reason"].eq("TP_2%").sum())
    sl = int(trades_df["pnl_pct"].lt(0).sum())

    net_pnl = float(trades_df["pnl_pct"].sum())
    avg_trade = float(trades_df["pnl_pct"].mean())
    win_rate = wins / total_trades * 100

    gross_profit = trades_df.loc[trades_df["pnl_pct"] > 0, "pnl_pct"].sum()
    gross_loss = -trades_df.loc[trades_df["pnl_pct"] < 0, "pnl_pct"].sum()
    profit_factor = (
        float(gross_profit / gross_loss)
        if gross_loss > 0 else float("inf")
    )

    equity = trades_df["pnl_pct"].cumsum()
    peak = equity.cummax()
    drawdown = equity - peak
    max_drawdown = float(drawdown.min())

    summary = {
        "Stocks Scanned": total,
        "Stocks With Trades": int(trades_df["symbol"].nunique()),
        "Total Trades": total_trades,
        "Wins": wins,
        "Losses": losses,
        "TP 2% Hits": tp,
        "SL Hits": sl,
        "Force Exit 15:10": force,
        "Win Rate %": round(win_rate, 2),
        "Net P&L %": round(net_pnl, 4),
        "Average Trade %": round(avg_trade, 4),
        "Profit Factor": round(profit_factor, 4) if math.isfinite(profit_factor) else "INF",
        "Max Drawdown %": round(max_drawdown, 4),
        "Total R": round(float(trades_df["R"].sum()), 4),
        "Elapsed Seconds": round(elapsed, 2),
        "Errors": len(errors),
    }

    summary_file = RESULT_DIR / "SMC_Backtest_Summary_12M.txt"
    with open(summary_file, "w", encoding="utf-8") as f:
        f.write("FAST NSE SMC V1 BACKTEST\n")
        f.write("=" * 60 + "\n")
        for k, v in summary.items():
            f.write(f"{k}: {v}\n")
        f.write("\nRULES\n")
        f.write("Timeframe: 15m\n")
        f.write("Entry cutoff: 14:45 IST\n")
        f.write("Target: +2%\n")
        f.write("Stop Loss: -1%\n")
        f.write("Force exit: 15:10 IST\n")
        f.write("Same 5m bar TP+SL: SL first\n")
        f.write("Historical data source: DhanHQ Intraday Historical API (15m/5m) + Daily Historical API.\n")
        f.write("Dhan intraday data is requested in 90-day chunks and cached locally.\n")
        f.write("\nNOTE\n")
        f.write(
            "Entry is generated from the 15m SMC candle. TP/SL/force-exit "
            "monitoring uses 5m data. The exact 15:10 price is represented "
            "by the OPEN of the 15:10 5m candle because OHLC history cannot "
            "reconstruct a transaction price inside a candle.\n"
        )
        f.write(
            "Daily MACD is calculated only from completed daily candles "
            "available before each entry date to avoid look-ahead bias.\n"
        )

    if errors:
        pd.DataFrame(errors).to_csv(
            RESULT_DIR / "backtest_errors.csv", index=False
        )

    print("\n" + "=" * 90)
    print("BACKTEST COMPLETE")
    print("=" * 90)
    for k, v in summary.items():
        print(f"{k:24}: {v}")

    print("\nOUTPUT FILES:")
    print(trade_file)
    print(stock_file)
    print(summary_file)
    if errors:
        print(RESULT_DIR / "backtest_errors.csv")


if __name__ == "__main__":
    main()
