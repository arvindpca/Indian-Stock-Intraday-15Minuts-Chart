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
from urllib.parse import quote

# ============================================================
# FAST NSE SMC V1 BACKTEST
# Universe: stock_names.txt beside this file
# Entry <= 14:45 IST
# TP = +2%, SL = -1%
# Force exit = 15:10 IST
# Entry logic: TradingView V1 SMC sequence from live scanner
# ============================================================

IST = ZoneInfo("Asia/Kolkata")
YAHOO_BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"
YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130 Safari/537.36"
}

# -------------------- BACKTEST SETTINGS ---------------------
ENTRY_CUTOFF = dtime(14, 45)
FORCE_EXIT_TIME = dtime(15, 10)
TP_PERCENT = 2.0
SL_PERCENT = 1.0

LOOKBACK_DAYS_15M = 30       # Yahoo intraday history available recently
DAILY_LOOKBACK_DAYS = 450
LOOKBACK_DAYS_5M = 30        # Same period as backtest; keeps Yahoo 5m request valid

MAX_WORKERS = 12
REQUEST_TIMEOUT = 20
RETRIES = 3

# -------------------- SECTOR TREND FILTER ------------------
# Sector is determined from NSE equity metadata at runtime and cached locally.
# Morning sector trend = equal-weight average return of mapped stocks in the
# first completed 15-minute candle of that trading day versus the prior close.
# Positive > +0.10%, Negative < -0.10%, otherwise Neutral.
SECTOR_TREND_THRESHOLD_PCT = 0.10
SECTOR_METADATA_CACHE = CACHE_DIR if "CACHE_DIR" in globals() else None
SECTOR_META_FILE_NAME = "nse_sector_map.json"

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
SECTOR_META_FILE = CACHE_DIR / SECTOR_META_FILE_NAME

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



def load_sector_map(symbols):
    """Load/calculate NSE sector mapping. Missing metadata remains UNKNOWN."""
    mapping = {}
    if SECTOR_META_FILE.exists():
        try:
            with open(SECTOR_META_FILE, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if isinstance(cached, dict):
                mapping.update({clean_symbol(k): str(v).strip() for k, v in cached.items() if v})
        except Exception:
            pass

    missing = [s for s in symbols if clean_symbol(s) not in mapping]
    if not missing:
        return mapping

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://www.nseindia.com/",
    })

    def one(sym):
        url = "https://www.nseindia.com/api/equity-meta-info"
        try:
            r = session.get(url, params={"symbol": clean_symbol(sym)}, timeout=15)
            r.raise_for_status()
            data = r.json()
            # NSE response versions use different names; prefer sector, then industry.
            sector = data.get("sector") or data.get("industry") or data.get("industryInfo")
            if isinstance(sector, dict):
                sector = sector.get("industry") or sector.get("sector") or sector.get("name")
            return clean_symbol(sym), str(sector).strip() if sector else "UNKNOWN"
        except Exception:
            return clean_symbol(sym), "UNKNOWN"

    log(f"Loading NSE sector mapping for {len(missing)} stocks...")
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, 8)) as ex:
        futs = [ex.submit(one, s) for s in missing]
        for fut in as_completed(futs):
            k, v = fut.result()
            mapping[k] = v

    try:
        with open(SECTOR_META_FILE, "w", encoding="utf-8") as f:
            json.dump(mapping, f, indent=2, sort_keys=True)
    except Exception:
        pass
    return mapping


def first_candle_sector_returns(symbol_raw15, sector_map):
    """Return {date: {sector: average first-15m return pct}}."""
    grouped = {}
    for symbol, df in symbol_raw15.items():
        sector = sector_map.get(clean_symbol(symbol), "UNKNOWN")
        if sector == "UNKNOWN":
            continue
        d = df.copy()
        d["time_ist"] = d["time"].dt.tz_convert(IST)
        d = d.sort_values("time_ist")
        # First completed 15m candle after 09:15. Yahoo timestamps are UTC internally.
        d = d[(d["time_ist"].dt.hour == 9) & (d["time_ist"].dt.minute == 15)]
        if d.empty:
            continue
        for _, row in d.iterrows():
            day = row["time_ist"].date().isoformat()
            # Prior close is unavailable from 15m alone; use the first candle's open as baseline.
            ret = (float(row["close"]) / float(row["open"]) - 1.0) * 100.0
            grouped.setdefault(day, {}).setdefault(sector, []).append(ret)
    out = {}
    for day, sectors in grouped.items():
        out[day] = {sec: float(np.mean(vals)) for sec, vals in sectors.items() if vals}
    return out


def sector_trend_for(day, sector, sector_returns):
    value = sector_returns.get(str(day), {}).get(sector)
    if value is None:
        return "UNKNOWN", None
    if value > SECTOR_TREND_THRESHOLD_PCT:
        return "POSITIVE", value
    if value < -SECTOR_TREND_THRESHOLD_PCT:
        return "NEGATIVE", value
    return "NEUTRAL", value


def sector_allows(direction, trend):
    if trend == "POSITIVE" and direction == "LONG":
        return True
    if trend == "NEGATIVE" and direction == "SHORT":
        return True
    return False


def yahoo_chart(symbol, interval, period1, period2):
    ticker = clean_symbol(symbol) + ".NS"
    url = YAHOO_BASE_URL + ticker
    params = {
        "interval": interval,
        "period1": int(period1),
        "period2": int(period2),
        "events": "history",
        "includePrePost": "false",
    }

    last_error = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(
                url, params=params, headers=YAHOO_HEADERS,
                timeout=REQUEST_TIMEOUT
            )
            r.raise_for_status()
            payload = r.json()
            results = (payload.get("chart") or {}).get("result") or []
            if not results:
                raise RuntimeError(f"Yahoo returned no data for {ticker}")

            result = results[0]
            timestamps = result.get("timestamp") or []
            quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]

            rows = []
            opens = quote.get("open") or []
            highs = quote.get("high") or []
            lows = quote.get("low") or []
            closes = quote.get("close") or []
            volumes = quote.get("volume") or []

            for i, ts in enumerate(timestamps):
                try:
                    o, h, l, c, v = opens[i], highs[i], lows[i], closes[i], volumes[i]
                    if None in (o, h, l, c, v):
                        continue
                    rows.append({
                        "time": pd.to_datetime(int(ts), unit="s", utc=True),
                        "open": float(o),
                        "high": float(h),
                        "low": float(l),
                        "close": float(c),
                        "volume": float(v),
                    })
                except (IndexError, TypeError, ValueError):
                    continue

            df = pd.DataFrame(rows)
            if df.empty:
                raise RuntimeError(f"Yahoo returned empty candles for {ticker}")

            return (
                df.sort_values("time")
                .drop_duplicates("time")
                .reset_index(drop=True)
            )

        except Exception as e:
            last_error = e
            if attempt < RETRIES:
                time.sleep(0.7 * attempt)

    raise RuntimeError(str(last_error))


def cached_chart(symbol, interval, days, cache_tag):
    today = datetime.now(IST).date().isoformat()
    cache_file = CACHE_DIR / f"{clean_symbol(symbol)}_{cache_tag}_{today}.pkl.gz"

    if cache_file.exists():
        try:
            return pd.read_pickle(cache_file, compression="gzip")
        except Exception:
            try:
                cache_file.unlink()
            except Exception:
                pass

    now = int(time.time())
    start = now - days * 86400
    df = yahoo_chart(symbol, interval, start, now)

    # Never use the currently forming 15m/5m candle.
    if interval in ("15m", "5m"):
        now_bucket = pd.Timestamp(datetime.now(IST)).tz_convert("UTC")
        if interval == "15m":
            minute = (now_bucket.minute // 15) * 15
        else:
            minute = (now_bucket.minute // 5) * 5
        current_bucket = now_bucket.replace(
            minute=minute, second=0, microsecond=0
        )
        df = df[df["time"] < current_bucket]

    df.to_pickle(cache_file, compression="gzip")
    return df.reset_index(drop=True)


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


def process_symbol(symbol, index, total, raw15, sector_map, sector_returns):
    try:
        candidates = detect_entries(symbol, raw15)
        if not candidates:
            log(f"[{index}/{total}] {symbol}: NO TRADE")
            return [], None

        daily = cached_chart(symbol, "1d", DAILY_LOOKBACK_DAYS, "1d")
        filtered = []
        sector = sector_map.get(clean_symbol(symbol), "UNKNOWN")
        for c in candidates:
            day = c["entry_time_ist"].date()
            trend, sector_ret = sector_trend_for(day, sector, sector_returns)
            c["sector"] = sector
            c["sector_trend"] = trend
            c["sector_return_pct"] = sector_ret
            if not sector_allows(c["direction"], trend):
                continue

            if USE_DAILY_MACD_FILTER:
                macd, macd_signal = daily_macd_as_of(daily, day)
                if macd is None:
                    continue
                direction = c["direction"]
                if not ((direction == "LONG" and macd > macd_signal) or
                        (direction == "SHORT" and macd < macd_signal)):
                    continue
                c["daily_macd"] = macd
                c["daily_macd_signal"] = macd_signal
            filtered.append(c)

        if not filtered:
            log(f"[{index}/{total}] {symbol}: SMC/SECTOR/MACD FILTER")
            return [], None

        raw5 = cached_chart(symbol, "5m", LOOKBACK_DAYS_5M, "5m_30d")
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

    print("=" * 90)
    print("NSE SMC V1 + SECTOR TREND FILTER | 1 MONTH BACKTEST")
    print("=" * 90)
    print(f"Watchlist             : {WATCHLIST_FILE.name}")
    print(f"Stocks                 : {total}")
    print(f"15M history            : {LOOKBACK_DAYS_15M} days")
    print(f"Sector trend           : first completed 15M candle")
    print(f"Positive/Negative band : +/- {SECTOR_TREND_THRESHOLD_PCT:.2f}%")
    print(f"Entry cutoff           : {ENTRY_CUTOFF.strftime('%H:%M')} IST")
    print(f"Target                 : +{TP_PERCENT:.2f}%")
    print(f"Stop loss              : -{SL_PERCENT:.2f}%")
    print(f"Force exit             : {FORCE_EXIT_TIME.strftime('%H:%M')} IST")
    print(f"Workers                : {MAX_WORKERS}")
    print(f"Cache                  : {CACHE_DIR}")
    print("=" * 90)

    started = time.time()
    errors = []

    # Phase 1: download 15M for every requested stock. This keeps the universe at all 828.
    raw15_map = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futs = {executor.submit(cached_chart, s, "15m", LOOKBACK_DAYS_15M, "15m"): s
                for s in symbols}
        for n, fut in enumerate(as_completed(futs), 1):
            sym = futs[fut]
            try:
                raw15_map[sym] = fut.result()
                log(f"[15M {n}/{total}] {sym}: OK")
            except Exception as e:
                errors.append({"symbol": sym, "error": str(e)})
                log(f"[15M {n}/{total}] {sym}: ERROR - {e}")

    sector_map = load_sector_map(symbols)
    sector_returns = first_candle_sector_returns(raw15_map, sector_map)

    all_trades = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futs = {
            executor.submit(process_symbol, sym, i, total, raw15_map[sym], sector_map, sector_returns): sym
            for i, sym in enumerate(symbols, 1) if sym in raw15_map
        }
        for fut in as_completed(futs):
            trades, error = fut.result()
            all_trades.extend(trades)
            if error:
                errors.append(error)

    elapsed = time.time() - started
    trades_df = pd.DataFrame(all_trades)

    if trades_df.empty:
        print("\nNO COMPLETED TRADES FOUND.")
        if errors:
            pd.DataFrame(errors).to_csv(RESULT_DIR / "backtest_errors.csv", index=False)
        return

    trades_df = trades_df.sort_values(["entry_time", "symbol"]).reset_index(drop=True)
    trades_df["cum_pnl_pct"] = trades_df["pnl_pct"].cumsum()
    trades_df["R"] = trades_df["pnl_pct"] / SL_PERCENT

    # Normal trade details output, plus sector columns for validation.
    preferred = [
        "symbol","sector","sector_trend","sector_return_pct","direction",
        "entry_time","entry","sl","tp","zone","rsi","volume","volume_sma",
        "daily_macd","daily_macd_signal","sweep_time","displacement_time","mss_time",
        "fvg_time","ob_time","retest_time","confirmation_time","exit_time","exit_price",
        "exit_reason","pnl_pct","cum_pnl_pct","R"
    ]
    trades_df = trades_df[[c for c in preferred if c in trades_df.columns] +
                          [c for c in trades_df.columns if c not in preferred]]
    trades_df.to_csv(RESULT_DIR / "SMC_Sector_Backtest_Trades.csv", index=False)

    wins = int((trades_df["pnl_pct"] > 0.000001).sum())
    losses = int((trades_df["pnl_pct"] < -0.000001).sum())
    breakeven = int(len(trades_df) - wins - losses)
    win_rate = wins / len(trades_df) * 100 if len(trades_df) else 0.0
    gross_profit = float(trades_df.loc[trades_df["pnl_pct"] > 0, "pnl_pct"].sum())
    gross_loss = abs(float(trades_df.loc[trades_df["pnl_pct"] < 0, "pnl_pct"].sum()))
    profit_factor = gross_profit / gross_loss if gross_loss else float("inf")
    net_pnl = float(trades_df["pnl_pct"].sum())
    avg_trade = float(trades_df["pnl_pct"].mean())
    max_dd = float((trades_df["cum_pnl_pct"] - trades_df["cum_pnl_pct"].cummax()).min())

    long_df = trades_df[trades_df["direction"] == "LONG"]
    short_df = trades_df[trades_df["direction"] == "SHORT"]
    def side_stats(df):
        if df.empty:
            return 0, 0, 0.0, 0.0
        return len(df), int((df["pnl_pct"] > 0.000001).sum()), float((df["pnl_pct"] > 0.000001).mean()*100), float(df["pnl_pct"].sum())
    lt, lw, lwr, lp = side_stats(long_df)
    st, sw, swr, sp = side_stats(short_df)

    summary = [
        "NSE SMC V1 + SECTOR TREND FILTER - 1 MONTH BACKTEST",
        "=" * 70,
        f"Stocks Scanned        : {total}",
        f"Stocks With 15M Data  : {len(raw15_map)}",
        f"Sector Mapped Stocks  : {sum(1 for s in symbols if sector_map.get(s, 'UNKNOWN') != 'UNKNOWN')}",
        f"Total Trades          : {len(trades_df)}",
        f"Winning Trades        : {wins}",
        f"Losing Trades         : {losses}",
        f"Breakeven             : {breakeven}",
        f"Win Rate              : {win_rate:.2f}%",
        f"TP 2% Hits            : {int((trades_df['exit_reason'] == 'TP_2%').sum())}",
        f"SL 1% Hits            : {int((trades_df['exit_reason'] == 'SL_1%').sum())}",
        f"Force Exit 15:10      : {int((trades_df['exit_reason'] == 'FORCE_EXIT_15:10').sum())}",
        f"Net P&L %             : {net_pnl:.4f}%",
        f"Average Trade %       : {avg_trade:.4f}%",
        f"Profit Factor         : {profit_factor:.3f}",
        f"Max Drawdown %        : {max_dd:.4f}%",
        f"Total R               : {float(trades_df['R'].sum()):.4f}",
        "",
        "LONG",
        f"Trades / Wins         : {lt} / {lw}",
        f"Win Rate              : {lwr:.2f}%",
        f"Net P&L %             : {lp:.4f}%",
        "",
        "SHORT",
        f"Trades / Wins         : {st} / {sw}",
        f"Win Rate              : {swr:.2f}%",
        f"Net P&L %             : {sp:.4f}%",
        "",
        f"Elapsed Seconds       : {elapsed:.1f}",
        f"Errors                : {len(errors)}",
        "",
        "Sector filter: POSITIVE -> LONG only; NEGATIVE -> SHORT only; NEUTRAL/UNKNOWN -> no new entry.",
    ]
    with open(RESULT_DIR / "SMC_Sector_Backtest_Summary.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(summary))
    if errors:
        pd.DataFrame(errors).to_csv(RESULT_DIR / "backtest_errors.csv", index=False)

    print("\n" + "\n".join(summary))
    print("\nTrade CSV   :", RESULT_DIR / "SMC_Sector_Backtest_Trades.csv")
    print("Summary TXT :", RESULT_DIR / "SMC_Sector_Backtest_Summary.txt")


if __name__ == "__main__":
    main()
