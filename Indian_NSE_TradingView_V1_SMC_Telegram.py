import os
import time
import json
import math
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
        f"✓ RSI(14): {result['rsi_15m']:.2f} {result['rsi_condition']}\n"
        f"✓ Daily MACD: {result['daily_macd']:.4f} vs Signal {result['daily_macd_signal']:.4f}\n"
        f"✓ Market Cap: ₹{result['market_cap_cr']:,.0f} Cr\n"
        f"✓ HTF Filter: {result['htf_status']}\n"
        f"✓ Trading Time: ALL TIME / 24x7\n\n"
        f"<b>Reason:</b> Complete TradingView V1 sequential setup\n\n"
        f"⚠️ Signal only — no automatic order placed."
    )


# ============================================================
# SETTINGS
# ============================================================
SCAN_START_HOUR = 9
SCAN_START_MINUTE = 15
SCAN_END_HOUR = 15
SCAN_END_MINUTE = 30

# No 5-minute refresh interval. The scanner runs continuously
# during the allowed window, with API pacing between requests.
API_DELAY_SECONDS = 0.40

# Universe: all NSE equity stocks with market cap >= ₹5,000 Cr.
MARKET_CAP_MIN_CR = 50000000000.0
MAX_STOCKS = 0  # 0 = all qualifying stocks

CANDLE_LIMIT_15M = 250
DAILY_HISTORY_DAYS = 450

SWING_LEFT = 2
SWING_RIGHT = 2

ATR_LENGTH = 14
MINIMUM_DISPLACEMENT_ATR = 1.0

USE_FVG = True
USE_OB = True
OB_LOOKBACK = 5

USE_VOLUME_FILTER = True
VOLUME_LENGTH = 20

# Added from the user's Chartink reference as extra confirmation.
USE_RSI_FILTER = True
RSI_LENGTH = 14
RSI_LONG_MIN = 50.0
RSI_SHORT_MAX = 50.0

USE_DAILY_MACD_FILTER = True
DAILY_MACD_FAST = 12
DAILY_MACD_SLOW = 26
DAILY_MACD_SIGNAL = 9

# Same as TradingView V1 default: OFF.
USE_HTF_FILTER = False
HIGHER_TIMEFRAME = "ONE_HOUR"
HIGHER_TF_EMA_LENGTH = 50

STOP_LOSS_PERCENT = 1.0
TAKE_PROFIT_PERCENT = 2.0

SETUP_EXPIRY_BARS = 12

NSE_BASE_URL = "https://www.nseindia.com"
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


# NSE uses Akamai/browser-session protection.  Keep one persistent session
# and initialise it with a real navigation request before API calls.
NSE_SESSION = requests.Session()
NSE_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9,en-US;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}
NSE_API_HEADERS = {
    "User-Agent": NSE_BROWSER_HEADERS["User-Agent"],
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-IN,en;q=0.9,en-US;q=0.8",
    "Referer": "https://www.nseindia.com/",
    "X-Requested-With": "XMLHttpRequest",
}


def init_nse_session():
    """Prime NSE cookies using a browser-like page navigation."""
    try:
        NSE_SESSION.headers.clear()
        NSE_SESSION.headers.update(NSE_BROWSER_HEADERS)
        page = NSE_SESSION.get(
            "https://www.nseindia.com/get-quotes/equity?symbol=SBIN",
            timeout=20,
        )
        print(f"NSE session: HTTP {page.status_code}, cookies={len(NSE_SESSION.cookies)}")
        return page.status_code == 200
    except Exception as e:
        print(f"NSE session init warning: {e}")
        return False


def _refresh_nse_session():
    try:
        NSE_SESSION.cookies.clear()
    except Exception:
        pass
    return init_nse_session()


def _first_number(value):
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    if isinstance(value, str):
        text = value.replace(",", "").replace("₹", "").strip()
        try:
            return float(text)
        except Exception:
            return None
    return None


def extract_market_cap_cr(payload):
    """Extract NSE total market cap in ₹ crore from quote-equity and NextApi responses."""
    if not isinstance(payload, dict):
        return None

    # NextApi getSymbolData normally returns: equityResponse[0]
    # with tradeInfo directly inside that object.
    candidates = [payload]
    equity_response = payload.get("equityResponse")
    if isinstance(equity_response, list):
        candidates.extend(x for x in equity_response if isinstance(x, dict))
    elif isinstance(equity_response, dict):
        candidates.append(equity_response)

    # Also support wrappers used by some NSE responses.
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.append(data)
    elif isinstance(data, list):
        candidates.extend(x for x in data if isinstance(x, dict))

    for obj in candidates:
        # NextApi structure: equityResponse[0].tradeInfo.totalMarketCap
        trade = obj.get("tradeInfo") or {}
        for key in ("totalMarketCap", "marketCap", "totalMarketCapitalization"):
            n = _first_number(trade.get(key))
            if n is not None:
                return n

        # quote-equity trade_info structure:
        # marketDeptOrderBook.tradeInfo.totalMarketCap
        nested = obj.get("marketDeptOrderBook") or {}
        nested_trade = nested.get("tradeInfo") or {}
        for key in ("totalMarketCap", "marketCap", "totalMarketCapitalization"):
            n = _first_number(nested_trade.get(key))
            if n is not None:
                return n

        # Some wrappers expose market cap directly.
        for key in (
            "totalMarketCap", "totalMarketCapitalization",
            "marketCapInCr", "marketCapitalizationInCr",
            "totalMarketCapInCr",
        ):
            n = _first_number(obj.get(key))
            if n is not None:
                return n

        # Older quote-equity valuation shape. Only explicit crore fields are
        # accepted so a rupee-denominated value is not misread as ₹ crore.
        pi = obj.get("priceInfo") or {}
        for key in ("marketCapInCr", "marketCapitalizationInCr"):
            n = _first_number(pi.get(key))
            if n is not None:
                return n

    return None


def _nse_json_get(url, params, referer=None):
    """GET an NSE JSON endpoint with one refresh/retry for protected responses."""
    headers = dict(NSE_API_HEADERS)
    if referer:
        headers["Referer"] = referer

    for attempt in range(3):
        try:
            r = NSE_SESSION.get(url, params=params, headers=headers, timeout=15)
            if r.status_code == 200:
                try:
                    return r.json()
                except Exception:
                    return None

            if r.status_code in (401, 403, 429, 500, 502, 503, 504):
                if attempt < 2:
                    time.sleep(1.0 + attempt * 1.5)
                    _refresh_nse_session()
                    continue
            return None
        except requests.RequestException:
            if attempt < 2:
                time.sleep(1.0 + attempt * 1.5)
                _refresh_nse_session()
    return None


def fetch_nse_market_cap(symbol):
    """Fetch total market cap using NSE NextApi first, then trade_info fallback."""
    base = symbol.replace("-EQ", "", 1).strip().upper()

    # Newer NSE NextApi: one request can contain total market cap.
    next_url = f"{NSE_BASE_URL}/api/NextApi/apiClient/GetQuoteApi"
    payload = _nse_json_get(
        next_url,
        {
            "functionName": "getSymbolData",
            "marketType": "N",
            "series": "EQ",
            "symbol": base,
        },
        referer=f"https://www.nseindia.com/get-quotes/equity?symbol={base}",
    )
    cap = extract_market_cap_cr(payload) if payload else None
    if cap is not None:
        return cap

    # Standard NSE trade_info endpoint.
    quote_url = f"{NSE_BASE_URL}/api/quote-equity"
    payload = _nse_json_get(
        quote_url,
        {"symbol": base, "section": "trade_info"},
        referer=f"https://www.nseindia.com/get-quotes/equity?symbol={base}",
    )
    cap = extract_market_cap_cr(payload) if payload else None
    if cap is not None:
        return cap

    return None


def filter_market_cap_universe(stocks):
    """Fetch NSE market cap and keep all stocks >= configured threshold."""
    init_nse_session()
    qualified = []
    failed = 0

    print(f"STAGE 1 ONLY: checking market cap for {len(stocks)} NSE equity symbols...")

    for n, stock in enumerate(stocks, 1):
        cap = fetch_nse_market_cap(stock["symbol"])
        if cap is None:
            failed += 1
            print(f"[{n}/{len(stocks)}] {stock['symbol']}: market cap unavailable")
        else:
            stock["market_cap_cr"] = cap
            if cap >= MARKET_CAP_MIN_CR:
                qualified.append(stock)
            print(f"[{n}/{len(stocks)}] {stock['symbol']}: ₹{cap:,.0f} Cr {'PASS' if cap >= MARKET_CAP_MIN_CR else 'SKIP'}")

        # Slightly slower pacing reduces NSE protection/rate-limit failures.
        time.sleep(0.35)

    qualified.sort(key=lambda x: x["market_cap_cr"], reverse=True)
    if MAX_STOCKS > 0:
        qualified = qualified[:MAX_STOCKS]

    print(
        f"Market-cap universe: {len(qualified)} stocks >= ₹{MARKET_CAP_MIN_CR:,.0f} Cr; "
        f"unavailable={failed}"
    )
    return qualified


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
# DAILY CANDLES / INDICATORS
# ============================================================
def fetch_daily_candles(client, token):
    return fetch_candles(client, token, "ONE_DAY", DAILY_HISTORY_DAYS)


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


# ============================================================
# CORE TRADINGVIEW V1 STATE MACHINE
# ============================================================
def scan_stock(client, stock, daily_cache):
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

    # Daily MACD is cached for the trading day to avoid repeated API calls.
    cache_key = stock["symbol"]
    if cache_key not in daily_cache:
        daily_raw = fetch_daily_candles(client, token)
        daily_cache[cache_key] = add_daily_indicators(daily_raw)
    daily = daily_cache[cache_key]
    if len(daily) < 40:
        return {"symbol": symbol, "signal": "NO TRADE", "reason": "Insufficient daily candles"}

    daily_last = daily.iloc[-1]
    daily_macd = float(daily_last["macd"])
    daily_macd_signal = float(daily_last["macd_signal"])
    daily_macd_long = daily_macd > daily_macd_signal
    daily_macd_short = daily_macd < daily_macd_signal

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

        rsi_value = float(row["rsi"]) if not pd.isna(row["rsi"]) else float("nan")
        rsi_pass = (
            not USE_RSI_FILTER
            or (direction == 1 and rsi_value > RSI_LONG_MIN)
            or (direction == -1 and rsi_value < RSI_SHORT_MAX)
        )
        macd_pass = (
            not USE_DAILY_MACD_FILTER
            or (direction == 1 and daily_macd_long)
            or (direction == -1 and daily_macd_short)
        )

        entry_allowed = (
            setup_state == 6
            and volume_pass
            and rsi_pass
            and macd_pass
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
                "rsi_15m": rsi_value,
                "rsi_condition": "> 50" if direction == 1 else "< 50",
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
        print(f" RSI(14)       : {result['rsi_15m']:.2f}")
        print(f" Daily MACD    : {result['daily_macd']:.4f} / {result['daily_macd_signal']:.4f}")
        print(f" Market Cap    : ₹{result['market_cap_cr']:,.0f} Cr")
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
    # ALL-TIME SCAN: no clock-time or weekday restriction.
    # The scanner continuously runs and starts the next scan pass immediately.
    return True


def seconds_until_next_start(now=None):
    # Kept for compatibility with the main loop.
    # ALL-TIME mode never waits for a market-open time.
    return 0


def send_morning_welcome(stock_count):
    today = datetime.now(IST).strftime("%d-%m-%Y")
    message = (
        "🌅 <b>GOOD MORNING!</b>\n\n"
        "🇮🇳 <b>Indian NSE SMC Scanner</b>\n"
        f"📅 Date: {today}\n"
        f"📊 Market Cap: ≥ ₹{MARKET_CAP_MIN_CR:,.0f} Cr\n"
        f"📈 Timeframe: 15 Minutes\n"
        f"🔎 Stocks: {stock_count}\n"
        "⏰ Scan Mode: ALL TIME / 24x7\n"
        "📌 RSI + Daily MACD: ON\n"
        "🚀 Scanner Status: STARTED\n\n"
        "⚠️ Signal Only — No Automatic Orders"
    )
    return send_telegram_message(message)


def main():
    print()
    print("=" * 78)
    print(" INDIAN NSE - TRADINGVIEW V1 SMC SCANNER")
    print(" Angel One SmartAPI + NSE Market Cap + Telegram")
    print(" Market Cap >= ₹5,000 Cr | RSI + Daily MACD ON")
    print(" Liquidity -> Sweep -> Displacement -> MSS -> FVG/OB")
    print(" -> Retest -> Confirmation -> Volume -> RSI -> MACD -> ENTRY")
    print(" Scan mode: ALL TIME / 24x7 | No 5-minute refresh")
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
            if client is not None:
                print(
                    f"[{now.strftime('%Y-%m-%d %H:%M:%S IST')}] "
                    "Outside scan window. No scan/API calls."
                )
            else:
                print(
                    f"[{now.strftime('%Y-%m-%d %H:%M:%S IST')}] "
                    "Scanner waiting. No scan/API calls."
                )
            try:
                time.sleep(min(60, seconds_until_next_start(now)))
            except KeyboardInterrupt:
                print("\nScanner stopped by user.")
                break
            continue

        # ========================================================
        # STAGE 1 — MARKET-CAP FILTER
        # Run once per IST calendar day.
        # Only stocks that PASS ₹5,000 Cr proceed to Stage 2.
        # ========================================================
        if current_day != now.date():
            current_day = now.date()
            welcome_sent_day = None
            daily_cache = {}
            print("\n" + "=" * 78)
            print("STAGE 1: BUILDING MARKET-CAP UNIVERSE")
            print(f"Filter: Market Cap >= ₹{MARKET_CAP_MIN_CR:,.0f} Cr")
            print("No 15M candles / SMC scan before this filter.")
            print("=" * 78)

            if client is None:
                client = get_client()

            instruments = load_instruments()
            all_nse = build_nse_equity_universe(instruments)
            print(f"NSE equity symbols available: {len(all_nse)}")

            stocks = filter_market_cap_universe(all_nse)

            if not stocks:
                raise RuntimeError(
                    f"No NSE stocks found with market cap >= ₹{MARKET_CAP_MIN_CR:,.0f} Cr."
                )

            print("=" * 78)
            print(f"STAGE 1 COMPLETE: {len(stocks)} stocks PASSED market-cap filter")
            print("STAGE 2 will scan ONLY these stocks.")
            print("=" * 78)

        if welcome_sent_day != current_day:
            send_morning_welcome(len(stocks))
            welcome_sent_day = current_day

        scan_number += 1
        print("\n" + "=" * 78)
        print(f" NSE SCAN #{scan_number} | {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')}")
        print(f" Stocks passed market-cap filter: {len(stocks)}")
        print(" ONLY market-cap PASS stocks are sent to 15M/SMC processing.")
        print("=" * 78)

        signal_count = 0

        for n, stock in enumerate(stocks, 1):
            # Stop immediately once 03:00 PM is reached.
            if not is_scan_window(datetime.now(IST)):
                print("03:00 PM reached. Scanning stopped for today.")
                break

            try:
                print(f"[{n}/{len(stocks)}] Scanning {stock['symbol']} ...")
                result = scan_stock(client, stock, daily_cache)
                print_result(result)

                if result.get("signal") in ("LONG", "SHORT"):
                    signal_count += 1
                    key = signal_key(result)
                    if key not in SENT_SIGNALS:
                        if send_telegram_message(build_telegram_message(result)):
                            SENT_SIGNALS.add(key)
                    else:
                        print("Telegram: DUPLICATE SIGNAL SKIPPED")

                time.sleep(API_DELAY_SECONDS)

            except Exception as e:
                print("-" * 78)
                print(f"{stock['symbol']} ERROR: {e}")
                print("-" * 78)
                time.sleep(1.0)

        print("\n" + "=" * 78)
        print("NSE SCAN PASS COMPLETE")
        print(f"Stocks in universe : {len(stocks)}")
        print(f"Entry signals      : {signal_count}")
        print("ALL-TIME MODE: Starting next pass immediately. No 5-minute refresh and no clock-time restriction.")
        print("=" * 78)

        if not is_scan_window(datetime.now(IST)):
            continue




if __name__ == "__main__":
    main()
