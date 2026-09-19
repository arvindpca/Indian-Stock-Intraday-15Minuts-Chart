import os
import time
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from dotenv import load_dotenv

from angel_client import get_client

# ============================================================
# INDIAN NSE - TRADINGVIEW V1 SMC MIRROR
# Angel One SmartAPI + Telegram
#
# Signal only. NO automatic order placement.
#
# Same sequential logic as TradingView V1:
# Liquidity Sweep -> Displacement -> MSS -> FVG/OB
# -> Retest -> Confirmation -> Volume -> Entry
#
# NO CLOCK-TIME RESTRICTION.
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
    direction = result["signal"]
    emoji = "🟢" if direction == "LONG" else "🔴"

    return (
        f"{emoji} <b>{direction} ENTRY SIGNAL</b>\n\n"
        f"<b>Symbol:</b> {result['symbol']}\n"
        f"<b>Exchange:</b> NSE\n"
        f"<b>Timeframe:</b> 15M\n"
        f"<b>Signal Candle:</b> {result['candle_time']}\n"
        f"<b>Message Time:</b> {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')}\n\n"
        f"<b>Entry:</b> ₹{result['entry']:.2f}\n"
        f"<b>SL:</b> ₹{result['sl']:.2f} ({STOP_LOSS_PERCENT:.1f}%)\n"
        f"<b>TP:</b> ₹{result['tp']:.2f} ({TAKE_PROFIT_PERCENT:.1f}%)\n"
        f"<b>RR:</b> 1:{result['rr']:.2f}\n\n"
        f"<b>SMC V1 CONDITIONS</b>\n"
        f"✓ Liquidity: ₹{result['liquidity_level']:.2f}\n"
        f"✓ Sweep: {result['sweep_type']}\n"
        f"✓ Displacement: {result['displacement_atr']:.2f} ATR\n"
        f"✓ MSS: ₹{result['mss_level']:.2f}\n"
        f"✓ Zone: {result['zone_type']}\n"
        f"✓ Retest: PASS\n"
        f"✓ Confirmation: PASS\n"
        f"✓ Volume: {result['volume']:.0f} > SMA {result['volume_sma']:.0f}\n"
        f"✓ HTF Filter: {result['htf_status']}\n"
        f"✓ Trading Time: NO RESTRICTION\n\n"
        f"<b>Reason:</b> Complete TradingView V1 sequential setup\n\n"
        f"⚠️ Signal only — no automatic order placed."
    )


# ============================================================
# SETTINGS
# ============================================================
REFRESH_MINUTES = 5
API_DELAY_SECONDS = 0.40

# 0 = all NSE F&O stocks found in Angel instrument master.
MAX_STOCKS = 0

CANDLE_LIMIT_15M = 250
CANDLE_LIMIT_1H = 250

SWING_LEFT = 2
SWING_RIGHT = 2

ATR_LENGTH = 14
MINIMUM_DISPLACEMENT_ATR = 1.0

USE_FVG = True
USE_OB = True
OB_LOOKBACK = 5

USE_VOLUME_FILTER = True
VOLUME_LENGTH = 20

# Same as TradingView V1 default: OFF.
USE_HTF_FILTER = False
HIGHER_TIMEFRAME = "ONE_HOUR"
HIGHER_TF_EMA_LENGTH = 50

STOP_LOSS_PERCENT = 1.0
TAKE_PROFIT_PERCENT = 2.0

# NO CLOCK-TIME RESTRICTION.
SETUP_EXPIRY_BARS = 12

INSTRUMENT_URL = (
    "https://margincalculator.angelone.in/"
    "OpenAPI_File/files/OpenAPIScripMaster.json"
)


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


def get_nse_fno_stocks(instruments):
    """
    Build the NSE cash-equity universe from the current Angel One
    instrument master.

    Angel One has changed/varied instrument-master fields over time,
    so do not depend only on instrumenttype == FUTSTK. We accept:
      1) explicit FUTSTK records, and
      2) NFO symbols ending in FUT.

    Cash symbols are matched primarily by NSE symbol (without -EQ),
    with the Angel `name` field as a secondary key.
    """
    nfo_names = set()
    nfo_symbols = set()

    nfo_count = 0
    fut_count = 0
    futstk_count = 0

    for item in instruments:
        if not isinstance(item, dict):
            continue

        exch = str(item.get("exch_seg", "")).strip().upper()
        if exch != "NFO":
            continue

        nfo_count += 1

        name = str(item.get("name", "")).strip().upper()
        symbol = str(item.get("symbol", "")).strip().upper()
        instrument_type = str(item.get("instrumenttype", "")).strip().upper()

        is_stock_future = (
            instrument_type == "FUTSTK"
            or symbol.endswith("FUT")
        )

        if not is_stock_future:
            continue

        fut_count += 1

        if instrument_type == "FUTSTK":
            futstk_count += 1

        # Prefer the explicit name. For masters where name is blank,
        # derive the underlying from a FUT symbol.
        if name:
            nfo_names.add(name)

        if symbol.endswith("FUT"):
            base = symbol[:-3].strip()
            if base:
                nfo_symbols.add(base)

    stocks = []

    for item in instruments:
        if not isinstance(item, dict):
            continue

        exch = str(item.get("exch_seg", "")).strip().upper()
        if exch not in ("NSE_CM", "NSE"):
            continue

        symbol = str(item.get("symbol", "")).strip().upper()
        name = str(item.get("name", "")).strip().upper()
        token = str(item.get("token", "")).strip()

        if not symbol.endswith("-EQ") or not token:
            continue

        base_symbol = symbol[:-3].strip()

        if (
            name in nfo_names
            or base_symbol in nfo_symbols
            or base_symbol == name and name in nfo_names
        ):
            stocks.append({
                "symbol": symbol,
                "name": name or base_symbol,
                "token": token,
            })

    unique = {}
    for stock in stocks:
        unique[stock["symbol"]] = stock

    stocks = sorted(unique.values(), key=lambda x: x["symbol"])

    if MAX_STOCKS > 0:
        stocks = stocks[:MAX_STOCKS]

    print(
        f"Instrument master diagnostics: NFO={nfo_count}, "
        f"stock futures={fut_count}, FUTSTK={futstk_count}, "
        f"NSE F&O equities={len(stocks)}"
    )

    return stocks


# ============================================================
# HISTORICAL CANDLES
# ============================================================
def fetch_candles(client, token, interval, days):
    """
    Angel One getCandleData returns:
    [timestamp, open, high, low, close, volume]
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

    response = client.getCandleData(params)

    if not response or not response.get("status"):
        message = response.get("message", "Unknown candle API error") if response else "Empty response"
        raise RuntimeError(f"Candle API failed: {message}")

    rows = response.get("data") or []

    if not rows:
        raise ValueError("No candle data returned")

    df = pd.DataFrame(
        rows,
        columns=[
            "time",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ],
    )

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["time"] = pd.to_datetime(
        df["time"],
        errors="coerce",
        utc=True,
    )

    df = (
        df.dropna()
        .sort_values("time")
        .drop_duplicates("time")
        .reset_index(drop=True)
    )

    return df


# ============================================================
# INDICATORS
# ============================================================
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

    # TradingView ta.atr() = Wilder RMA.
    d["atr"] = tr.ewm(
        alpha=1.0 / ATR_LENGTH,
        adjust=False,
        min_periods=ATR_LENGTH,
    ).mean()

    d["volume_sma"] = d["volume"].rolling(
        VOLUME_LENGTH
    ).mean()

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


# ============================================================
# CORE TRADINGVIEW V1 STATE MACHINE
# ============================================================
def scan_stock(client, stock):
    symbol = stock["symbol"]
    token = stock["token"]

    raw = fetch_candles(
        client,
        token,
        "FIFTEEN_MINUTE",
        days=30,
    )

    d = add_indicators(raw)
    d = calculate_confirmed_pivots(d)

    # Ignore current/open candle.
    last_closed = len(d) - 2

    if last_closed < 50:
        return {
            "symbol": symbol,
            "signal": "NO TRADE",
            "reason": "Insufficient closed candles",
        }

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
                and row["volume"] > volume_sma
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

        entry_allowed = (
            setup_state == 6
            and volume_pass
            and htf_pass
        )

        if entry_allowed:
            entry = float(row["close"])

            if direction == 1:
                signal = "LONG"
                sl = entry * (1.0 - STOP_LOSS_PERCENT / 100.0)
                tp = entry * (1.0 + TAKE_PROFIT_PERCENT / 100.0)
                sweep_type = "SSL SWEEP"
                mss_level = mss_high
            else:
                signal = "SHORT"
                sl = entry * (1.0 + STOP_LOSS_PERCENT / 100.0)
                tp = entry * (1.0 - TAKE_PROFIT_PERCENT / 100.0)
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
                "rr": TAKE_PROFIT_PERCENT / STOP_LOSS_PERCENT,
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
    print()
    print("=" * 78)
    print(f" {result['symbol']}")

    if result["signal"] in ("LONG", "SHORT"):
        print(f" SIGNAL        : {result['signal']}")
        print(f" ENTRY         : ₹{result['entry']:.2f}")
        print(f" SL            : ₹{result['sl']:.2f}")
        print(f" TP 1:2        : ₹{result['tp']:.2f}")
        print(f" Zone          : {result['zone_type']}")
        print(f" Sweep         : {result['sweep_type']}")
        print(f" Displacement  : {result['displacement_atr']:.2f} ATR")
        print(f" Volume        : {result['volume']:.0f} / {result['volume_sma']:.0f}")
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
def main():
    print()
    print("=" * 78)
    print(" INDIAN NSE - TRADINGVIEW V1 SMC MIRROR")
    print(" Angel One SmartAPI | NSE F&O Stocks")
    print(" Liquidity -> Sweep -> Displacement -> MSS -> FVG/OB")
    print(" -> Retest -> Confirmation -> Volume -> ENTRY")
    print(" TIME RESTRICTION: DISABLED (ALL DAY)")
    print("=" * 78)

    print(
        "Current:",
        datetime.now(IST).strftime(
            "%Y-%m-%d %H:%M:%S IST"
        ),
    )
    print(f"Refresh: {REFRESH_MINUTES} minutes")
    print(f"Volume filter: {USE_VOLUME_FILTER}")
    print(f"HTF filter: {USE_HTF_FILTER}")
    print("=" * 78)

    client = get_client()

    instruments = load_instruments()
    stocks = get_nse_fno_stocks(instruments)

    if not stocks:
        raise RuntimeError(
            "No NSE F&O stocks found in Angel One instrument master."
        )

    print(f"NSE F&O stocks found: {len(stocks)}")

    if MAX_STOCKS > 0:
        print(f"MAX_STOCKS: {MAX_STOCKS}")

    scan_number = 0

    while True:
        scan_number += 1

        print()
        print("=" * 78)
        print(f" NSE F&O GLOBAL SCAN #{scan_number}")
        print(
            datetime.now(IST).strftime(
                "%Y-%m-%d %H:%M:%S IST"
            )
        )
        print("=" * 78)

        signal_count = 0

        for n, stock in enumerate(stocks, 1):
            try:
                print(
                    f"[{n}/{len(stocks)}] "
                    f"Scanning {stock['symbol']} ..."
                )

                result = scan_stock(client, stock)
                print_result(result)

                if result.get("signal") in ("LONG", "SHORT"):
                    signal_count += 1
                    key = signal_key(result)

                    if key not in SENT_SIGNALS:
                        message = build_telegram_message(result)

                        if send_telegram_message(message):
                            SENT_SIGNALS.add(key)
                    else:
                        print(
                            "Telegram: DUPLICATE SIGNAL SKIPPED"
                        )

                # Stay below aggressive API request rates.
                time.sleep(API_DELAY_SECONDS)

            except Exception as e:
                print()
                print("-" * 78)
                print(f"{stock['symbol']} ERROR: {e}")
                print("-" * 78)

                # Give API a little breathing room after errors.
                time.sleep(1.0)

        print()
        print("=" * 78)
        print("NSE F&O SCAN COMPLETE")
        print(f"Stocks scanned : {len(stocks)}")
        print(f"Entry signals  : {signal_count}")
        print(
            f"Next refresh in {REFRESH_MINUTES} minutes..."
        )
        print("=" * 78)

        try:
            time.sleep(REFRESH_MINUTES * 60)
        except KeyboardInterrupt:
            print("\nScanner stopped by user.")
            break


if __name__ == "__main__":
    main()
