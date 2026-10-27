#!/usr/bin/env python3
"""
VWAP + RSI Intraday Algo  -  NSE Equity Cash (Nifty 50 universe only)
=====================================================================

Pipeline
--------
1. 09:16 IST  : rank Nifty 50 by % change vs previous close, keep stocks with
                high relative volume, pick top-2 gainers (> +1.5 %) and
                top-2 losers (< -1.5 %).
2. Data       : 1-minute candles (history + today) are resampled into clean
                5-minute candles with pandas. Only *closed* candles are used.
3. Signals    : VWAP (session-anchored) + RSI(14) via pandas_ta
                (built-in fallback with identical maths if pandas_ta is
                not installed).
                BUY   : close crosses ABOVE VWAP and RSI > 60
                SHORT : close crosses BELOW VWAP and RSI < 40
4. Risk       : market entry on candle close, SL 0.5 %, target 1.0 % (1:2),
                hard square-off of everything at >= 15:15 IST, then stop.

Run
---
    pip install pandas numpy            # pandas_ta optional
    python vwap_rsi_algo.py --mode mock                 # full simulated day
    python vwap_rsi_algo.py --mode angel                # Angel data, PAPER orders
    python vwap_rsi_algo.py --mode angel --live         # Angel data, REAL orders

HOW TO PLUG IN A REAL BROKER
----------------------------
The algo only talks to two small interfaces:

    DataSource.get_quotes(symbols)            -> DataFrame[ltp, prev_close, volume]
    DataSource.get_candles(symbol, start, end)-> 1-min OHLCV DataFrame (IST index)
    DataSource.get_ltp(symbol)                -> float
    Broker.place_order(symbol, side, qty, ref_price) -> OrderResult

* Angel One SmartAPI : ready template below (AngelOneDataSource / AngelOneBroker).
  Needs: pip install smartapi-python pyotp requests
  Env vars: ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_PIN, ANGEL_TOTP_SECRET
* Zerodha Kite Connect : subclass DataSource / Broker and map
    get_quotes  -> kite.quote(["NSE:RELIANCE", ...])  (last_price, ohlc.close, volume)
    get_candles -> kite.historical_data(instrument_token, start, end, "minute")
    get_ltp     -> kite.ltp(["NSE:RELIANCE"])
    place_order -> kite.place_order(variety=kite.VARIETY_REGULAR, exchange="NSE",
                       tradingsymbol=sym, transaction_type=side, quantity=qty,
                       product=kite.PRODUCT_MIS, order_type=kite.ORDER_TYPE_MARKET)
* Alice Blue : same idea with its quote / historical / place_order endpoints.
"""

from __future__ import annotations

import abc
import argparse
import csv
import logging
import math
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

try:  # pandas_ta is optional - its last releases break on some numpy versions
    import pandas_ta as ta  # type: ignore

    HAS_PANDAS_TA = True
except Exception:  # noqa: BLE001
    ta = None
    HAS_PANDAS_TA = False

IST = ZoneInfo("Asia/Kolkata")
ONE_MIN = pd.Timedelta(minutes=1)

# Nifty 50 constituents - index composition changes twice a year, keep updated.
NIFTY50 = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL",
    "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL",
    "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HEROMOTOCO",
    "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDUSINDBK", "INFY",
    "ITC", "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LT",
    "M&M", "MARUTI", "NESTLEIND", "NTPC", "ONGC",
    "POWERGRID", "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN",
    "SUNPHARMA", "TATACONSUM", "TATAMOTORS", "TATASTEEL", "TCS",
    "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO",
]

log = logging.getLogger("vwap_rsi")


# --------------------------------------------------------------------------- #
# Config and models
# --------------------------------------------------------------------------- #
@dataclass
class StrategyConfig:
    universe: list[str] = field(default_factory=lambda: list(NIFTY50))
    market_open: dtime = dtime(9, 15)
    selection_time: dtime = dtime(9, 16)
    square_off_time: dtime = dtime(15, 15)
    # Selection
    gainer_threshold_pct: float = 1.5
    loser_threshold_pct: float = -1.5
    top_n: int = 2
    min_relative_volume: float = 1.5     # today vol / avg vol of same window
    history_days: int = 5                # for RSI warm-up and RVOL baseline
    # Indicators / signals
    candle_minutes: int = 5
    rsi_period: int = 14
    rsi_buy_level: float = 60.0
    rsi_short_level: float = 40.0
    # Risk (hardcoded rules from the spec)
    stop_loss_pct: float = 0.5
    target_pct: float = 1.0
    # Execution
    capital_per_trade: float = 100_000.0
    max_trades_per_symbol: int = 2
    poll_seconds: int = 5
    candle_close_buffer_seconds: int = 3  # wait for broker to publish the candle
    trade_log_path: str = "trades.csv"


@dataclass
class OrderResult:
    order_id: str
    fill_price: float


@dataclass
class Position:
    symbol: str
    side: str            # "LONG" / "SHORT"
    qty: int
    entry_price: float
    entry_time: datetime
    stop_loss: float
    target: float
    order_id: str


@dataclass
class Trade:
    symbol: str
    side: str
    qty: int
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    exit_reason: str
    pnl: float


class DataSourceError(Exception):
    """Raised when market data cannot be fetched."""


class OrderError(Exception):
    """Raised when an order is rejected / fails."""


# --------------------------------------------------------------------------- #
# Clock (real or simulated) - lets the same algo run live or in a mock day
# --------------------------------------------------------------------------- #
class Clock(abc.ABC):
    @abc.abstractmethod
    def now(self) -> datetime: ...

    @abc.abstractmethod
    def sleep(self, seconds: float) -> None: ...


class RealClock(Clock):
    def now(self) -> datetime:
        return datetime.now(IST)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class SimClock(Clock):
    def __init__(self, start: datetime):
        self._now = start

    def now(self) -> datetime:
        return self._now

    def sleep(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


# --------------------------------------------------------------------------- #
# Interfaces
# --------------------------------------------------------------------------- #
class DataSource(abc.ABC):
    @abc.abstractmethod
    def get_quotes(self, symbols: list[str]) -> pd.DataFrame:
        """Index = symbol, columns = ltp, prev_close, volume (today cumulative)."""

    @abc.abstractmethod
    def get_candles(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        """Closed 1-minute candles in [start, end). Index tz-aware IST,
        columns open, high, low, close, volume."""

    def get_ltp(self, symbol: str) -> float:
        quotes = self.get_quotes([symbol])
        return float(quotes.loc[symbol, "ltp"])


class Broker(abc.ABC):
    @abc.abstractmethod
    def place_order(self, symbol: str, side: str, qty: int, ref_price: float) -> OrderResult:
        """side = 'BUY' or 'SELL'. Market order, intraday product."""


def only_closed_bars(df: pd.DataFrame, end: datetime) -> pd.DataFrame:
    """Drop the still-forming 1-min candle (bar start + 1 min must be <= end)."""
    if df.empty:
        return df
    return df[df.index + ONE_MIN <= pd.Timestamp(end)]


# --------------------------------------------------------------------------- #
# Indicators
# --------------------------------------------------------------------------- #
def resample_ohlcv(df_1m: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """1-min -> N-min candles. NSE session starts 09:15, which is a multiple of
    5/15 minutes, so default day-origin bins align to 09:15, 09:20, ..."""
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    out = df_1m.resample(f"{minutes}min", label="left", closed="left").agg(agg)
    return out.dropna(subset=["open"])


def _rsi_fallback(close: pd.Series, length: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    return rsi.where(avg_loss != 0, 100.0)


def _vwap_fallback(df: pd.DataFrame) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3
    day = df.index.date
    cum_pv = (typical * df["volume"]).groupby(day).cumsum()
    cum_v = df["volume"].groupby(day).cumsum()
    return cum_pv / cum_v.replace(0, np.nan)


def add_indicators(df: pd.DataFrame, rsi_period: int) -> pd.DataFrame:
    """Adds 'rsi' and session-anchored 'vwap' columns."""
    df = df.copy()
    rsi = vwap = None
    if HAS_PANDAS_TA:
        try:
            rsi = ta.rsi(df["close"], length=rsi_period)
            vwap = ta.vwap(df["high"], df["low"], df["close"], df["volume"], anchor="D")
        except Exception as exc:  # noqa: BLE001
            log.debug("pandas_ta failed (%s); using fallback", exc)
            rsi = vwap = None
    df["rsi"] = rsi if rsi is not None else _rsi_fallback(df["close"], rsi_period)
    df["vwap"] = vwap if vwap is not None else _vwap_fallback(df)
    return df


# --------------------------------------------------------------------------- #
# The algo
# --------------------------------------------------------------------------- #
class IntradayAlgo:
    def __init__(self, config: StrategyConfig, data: DataSource, broker: Broker, clock: Clock):
        self.cfg = config
        self.data = data
        self.broker = broker
        self.clock = clock

        self.today: date = clock.now().date()
        self.watchlist: list[str] = []
        self.side_hint: dict[str, str] = {}
        self.positions: dict[str, Position] = {}
        self.closed_trades: list[Trade] = []
        self.trade_counts: dict[str, int] = {}
        self.history: dict[str, pd.DataFrame] = {}
        self.volume_baseline = pd.Series(dtype=float)
        self._last_eval_candle: dict[str, pd.Timestamp] = {}
        self.halted = False

    # ---------------------------------------------------------------- utils
    def _at(self, t: dtime) -> datetime:
        return datetime.combine(self.today, t, tzinfo=IST)

    def _safe_call(self, desc: str, fn: Callable[..., Any], *args: Any,
                   retries: int = 3, delay: float = 1.0, **kwargs: Any) -> Any:
        """Calls an API function with retry + logging. Returns None on failure."""
        for attempt in range(1, retries + 1):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - broker SDKs raise anything
                log.warning("%s failed (attempt %d/%d): %s", desc, attempt, retries, exc)
                if attempt < retries:
                    self.clock.sleep(delay * attempt)
        log.error("%s failed after %d attempts", desc, retries)
        return None

    def _sleep_until(self, target: datetime) -> None:
        while (remaining := (target - self.clock.now()).total_seconds()) > 0:
            self.clock.sleep(min(remaining, 30))

    # ------------------------------------------------------------- pipeline
    def run(self) -> None:
        log.info("Algo start %s | pandas_ta=%s", self.clock.now().strftime("%Y-%m-%d %H:%M:%S"),
                 HAS_PANDAS_TA)
        try:
            self.prepare_day()
            self._sleep_until(self._at(self.cfg.selection_time))
            if self.clock.now() >= self._at(self.cfg.square_off_time):
                log.warning("Started after square-off time; nothing to do today.")
                return
            self.select_stocks()
            if not self.watchlist:
                log.info("No stock passed the filter today. Exiting.")
                return
            self._main_loop()
        except KeyboardInterrupt:
            log.warning("Interrupted by user.")
        except Exception:  # noqa: BLE001
            log.exception("Unexpected error in main loop")
        finally:
            if self.positions:
                log.warning("Shutting down with open positions -> squaring off.")
                self.square_off_all("SHUTDOWN")
            self.report()

    def prepare_day(self) -> None:
        """Fetch last N sessions of 1-min data: RSI warm-up + RVOL baseline."""
        start = self._at(self.cfg.market_open) - timedelta(days=self.cfg.history_days * 2 + 4)
        end = self._at(self.cfg.market_open)
        window_end = self.cfg.selection_time
        baseline = {}
        for sym in self.cfg.universe:
            df = self._safe_call(f"history {sym}", self.data.get_candles, sym, start, end)
            if df is None or df.empty:
                continue
            dates = sorted(set(df.index.date))[-self.cfg.history_days:]
            df = df[np.isin(df.index.date, dates)]
            self.history[sym] = df
            t = df.index.time
            opening = df[(t >= self.cfg.market_open) & (t < window_end)]
            per_day = opening.groupby(opening.index.date)["volume"].sum()
            if len(per_day):
                baseline[sym] = per_day.mean()
        self.volume_baseline = pd.Series(baseline, dtype=float)
        log.info("History loaded for %d/%d symbols", len(self.history), len(self.cfg.universe))

    def select_stocks(self) -> None:
        snap = self._safe_call("quotes", self.data.get_quotes, self.cfg.universe)
        if snap is None or snap.empty:
            log.error("Could not fetch quotes for selection.")
            return
        snap = snap.copy()
        snap["change_pct"] = (snap["ltp"] / snap["prev_close"] - 1) * 100
        snap["rvol"] = snap["volume"] / self.volume_baseline.reindex(snap.index)
        snap = snap.dropna(subset=["change_pct", "rvol"])
        active = snap[snap["rvol"] >= self.cfg.min_relative_volume]

        gainers = active[active["change_pct"] > self.cfg.gainer_threshold_pct] \
            .nlargest(self.cfg.top_n, "change_pct")
        losers = active[active["change_pct"] < self.cfg.loser_threshold_pct] \
            .nsmallest(self.cfg.top_n, "change_pct")

        for sym in gainers.index:
            self.side_hint[sym] = "GAINER"
        for sym in losers.index:
            self.side_hint[sym] = "LOSER"
        self.watchlist = list(gainers.index) + list(losers.index)

        cols = ["ltp", "prev_close", "change_pct", "rvol"]
        log.info("Selection @ %s\nGainers:\n%s\nLosers:\n%s",
                 self.clock.now().strftime("%H:%M:%S"),
                 gainers[cols].round(2).to_string() if len(gainers) else "  none",
                 losers[cols].round(2).to_string() if len(losers) else "  none")

    def _main_loop(self) -> None:
        sq_off = self._at(self.cfg.square_off_time)
        while not self.halted:
            now = self.clock.now()
            if now >= sq_off:
                self.square_off_all("TIME_EXIT_15:15")
                self.halted = True
                log.info("15:15 reached. Trading stopped for the day.")
                break
            self.manage_positions()
            candle_start = self._latest_closed_candle(now)
            if candle_start is not None:
                self.scan_for_entries(candle_start)
            self.clock.sleep(self.cfg.poll_seconds)

    # -------------------------------------------------------------- signals
    def _latest_closed_candle(self, now: datetime) -> Optional[pd.Timestamp]:
        effective = now - timedelta(seconds=self.cfg.candle_close_buffer_seconds)
        elapsed = (effective - self._at(self.cfg.market_open)).total_seconds() / 60
        k = math.floor(elapsed / self.cfg.candle_minutes)
        if k < 1:
            return None
        start = self._at(self.cfg.market_open) + timedelta(minutes=(k - 1) * self.cfg.candle_minutes)
        return pd.Timestamp(start)

    def get_5min_frame(self, symbol: str, candle_end: datetime) -> Optional[pd.DataFrame]:
        today_1m = self._safe_call(f"candles {symbol}", self.data.get_candles, symbol,
                                   self._at(self.cfg.market_open), candle_end)
        if today_1m is None or today_1m.empty:
            return None
        hist = self.history.get(symbol, pd.DataFrame())
        df_1m = pd.concat([hist, today_1m])
        df_1m = df_1m[~df_1m.index.duplicated(keep="last")].sort_index()
        df_5m = resample_ohlcv(df_1m, self.cfg.candle_minutes)
        df_5m = df_5m[df_5m.index + pd.Timedelta(minutes=self.cfg.candle_minutes)
                      <= pd.Timestamp(candle_end)]
        return add_indicators(df_5m, self.cfg.rsi_period)

    @staticmethod
    def generate_signal(df: pd.DataFrame, rsi_buy: float, rsi_short: float) -> Optional[str]:
        """Looks only at the last closed candle vs the one before (same session)."""
        if len(df) < 2:
            return None
        prev, last = df.iloc[-2], df.iloc[-1]
        if prev.name.date() != last.name.date():
            return None  # VWAP resets daily; no cross across sessions
        if pd.isna([prev.vwap, last.vwap, last.rsi]).any():
            return None
        crossed_up = prev.close <= prev.vwap and last.close > last.vwap
        crossed_down = prev.close >= prev.vwap and last.close < last.vwap
        if crossed_up and last.rsi > rsi_buy:
            return "BUY"
        if crossed_down and last.rsi < rsi_short:
            return "SHORT"
        return None

    def scan_for_entries(self, candle_start: pd.Timestamp) -> None:
        candle_end = (candle_start + pd.Timedelta(minutes=self.cfg.candle_minutes)).to_pydatetime()
        for sym in self.watchlist:
            if self._last_eval_candle.get(sym) == candle_start:
                continue
            df = self.get_5min_frame(sym, candle_end)
            if df is None or df.empty or df.index[-1] != candle_start:
                continue  # data not published yet -> retry on next poll
            self._last_eval_candle[sym] = candle_start
            last = df.iloc[-1]
            log.debug("%s %s close=%.2f vwap=%.2f rsi=%.1f", sym, candle_start.strftime("%H:%M"),
                      last.close, last.vwap, last.rsi)

            if sym in self.positions:
                continue
            if self.trade_counts.get(sym, 0) >= self.cfg.max_trades_per_symbol:
                continue
            signal = self.generate_signal(df, self.cfg.rsi_buy_level, self.cfg.rsi_short_level)
            if signal:
                log.info("SIGNAL %s %s @ candle %s close=%.2f vwap=%.2f rsi=%.1f",
                         signal, sym, candle_start.strftime("%H:%M"),
                         last.close, last.vwap, last.rsi)
                self.enter_position(sym, signal, float(last.close))

    # ------------------------------------------------------------ execution
    def enter_position(self, symbol: str, signal: str, candle_close: float) -> None:
        ltp = self._safe_call(f"ltp {symbol}", self.data.get_ltp, symbol) or candle_close
        qty = max(1, int(self.cfg.capital_per_trade // ltp))
        side = "BUY" if signal == "BUY" else "SELL"
        result = self._safe_call(f"entry order {symbol}", self.broker.place_order,
                                 symbol, side, qty, ltp, retries=1)
        if result is None:
            return
        price = result.fill_price
        sl_frac, tgt_frac = self.cfg.stop_loss_pct / 100, self.cfg.target_pct / 100
        if signal == "BUY":
            pos_side, sl, tgt = "LONG", price * (1 - sl_frac), price * (1 + tgt_frac)
        else:
            pos_side, sl, tgt = "SHORT", price * (1 + sl_frac), price * (1 - tgt_frac)
        self.positions[symbol] = Position(symbol, pos_side, qty, price, self.clock.now(),
                                          round(sl, 2), round(tgt, 2), result.order_id)
        self.trade_counts[symbol] = self.trade_counts.get(symbol, 0) + 1
        log.info("ENTRY %s %s qty=%d @ %.2f | SL %.2f | TGT %.2f",
                 pos_side, symbol, qty, price, sl, tgt)

    def manage_positions(self) -> None:
        for sym, pos in list(self.positions.items()):
            ltp = self._safe_call(f"ltp {sym}", self.data.get_ltp, sym, retries=1)
            if ltp is None:
                continue
            if pos.side == "LONG":
                if ltp <= pos.stop_loss:
                    self.exit_position(sym, ltp, "STOP_LOSS")
                elif ltp >= pos.target:
                    self.exit_position(sym, ltp, "TARGET")
            else:
                if ltp >= pos.stop_loss:
                    self.exit_position(sym, ltp, "STOP_LOSS")
                elif ltp <= pos.target:
                    self.exit_position(sym, ltp, "TARGET")

    def exit_position(self, symbol: str, ref_price: float, reason: str,
                      retries: int = 3) -> bool:
        pos = self.positions[symbol]
        side = "SELL" if pos.side == "LONG" else "BUY"
        result = self._safe_call(f"exit order {symbol}", self.broker.place_order,
                                 symbol, side, pos.qty, ref_price, retries=retries)
        if result is None:
            log.critical("EXIT FAILED for %s - CHECK BROKER TERMINAL / SQUARE OFF MANUALLY", symbol)
            return False
        direction = 1 if pos.side == "LONG" else -1
        pnl = (result.fill_price - pos.entry_price) * pos.qty * direction
        self.closed_trades.append(Trade(symbol, pos.side, pos.qty, pos.entry_time, pos.entry_price,
                                        self.clock.now(), result.fill_price, reason, round(pnl, 2)))
        del self.positions[symbol]
        log.info("EXIT %s %s @ %.2f (%s) PnL=%.2f", pos.side, symbol, result.fill_price, reason, pnl)
        return True

    def square_off_all(self, reason: str) -> None:
        for sym in list(self.positions):
            ltp = self._safe_call(f"ltp {sym}", self.data.get_ltp, sym) \
                or self.positions[sym].entry_price
            self.exit_position(sym, ltp, reason, retries=5)

    # -------------------------------------------------------------- report
    def report(self) -> None:
        if not self.closed_trades:
            log.info("No trades today.")
            return
        df = pd.DataFrame([t.__dict__ for t in self.closed_trades])
        df["entry_time"] = pd.to_datetime(df["entry_time"]).dt.strftime("%H:%M")
        df["exit_time"] = pd.to_datetime(df["exit_time"]).dt.strftime("%H:%M")
        wins = (df["pnl"] > 0).sum()
        log.info("Trades today:\n%s\nTotal PnL: %.2f | Win rate: %d/%d",
                 df.to_string(index=False), df["pnl"].sum(), wins, len(df))
        try:
            new_file = not os.path.exists(self.cfg.trade_log_path)
            with open(self.cfg.trade_log_path, "a", newline="") as fh:
                writer = csv.writer(fh)
                if new_file:
                    writer.writerow(["date", *df.columns])
                for row in df.itertuples(index=False):
                    writer.writerow([self.today.isoformat(), *row])
        except OSError as exc:
            log.error("Could not write trade log: %s", exc)


# --------------------------------------------------------------------------- #
# Paper broker
# --------------------------------------------------------------------------- #
class PaperBroker(Broker):
    def __init__(self, slippage_pct: float = 0.0):
        self.slippage = slippage_pct / 100
        self._n = 0

    def place_order(self, symbol: str, side: str, qty: int, ref_price: float) -> OrderResult:
        if qty <= 0 or ref_price <= 0:
            raise OrderError(f"Invalid order {symbol} qty={qty} price={ref_price}")
        self._n += 1
        adj = 1 + self.slippage if side == "BUY" else 1 - self.slippage
        return OrderResult(f"PAPER-{self._n:04d}", round(ref_price * adj, 2))


# --------------------------------------------------------------------------- #
# Mock data source (synthetic but realistic-shaped 1-min data, no look-ahead)
# --------------------------------------------------------------------------- #
class MockDataSource(DataSource):
    SESSION_MINUTES = 375  # 09:15 -> 15:30

    def __init__(self, symbols: list[str], clock: Clock, history_days: int = 6, seed: int = 7):
        self.clock = clock
        self.rng = np.random.default_rng(seed)
        today = clock.now().date()
        days, d = [], today
        while len(days) < history_days:
            d -= timedelta(days=1)
            if d.weekday() < 5:
                days.append(d)
        self.days = sorted(days) + [today]
        self.today = today
        gappers = self.rng.choice(len(symbols), size=8, replace=False)
        self.bars: dict[str, pd.DataFrame] = {}
        self.prev_close: dict[str, float] = {}
        for i, sym in enumerate(symbols):
            gap = 0.0
            if i in gappers[:4]:
                gap = self.rng.uniform(0.018, 0.035)
            elif i in gappers[4:]:
                gap = -self.rng.uniform(0.018, 0.035)
            self.bars[sym] = self._generate(sym, gap)

    def _generate(self, sym: str, today_gap: float) -> pd.DataFrame:
        rng = self.rng
        price = rng.uniform(200, 3000)
        base_vol = rng.uniform(2_000, 20_000)
        frames = []
        for day in self.days:
            is_today = day == self.today
            if is_today:
                self.prev_close[sym] = price
                price *= 1 + (today_gap if today_gap else rng.normal(0, 0.004))
            else:
                price *= 1 + rng.normal(0, 0.004)
            m = np.arange(self.SESSION_MINUTES)
            drift = np.repeat(rng.normal(0, 0.00035, 7), 60)[: self.SESSION_MINUTES]
            rets = drift + rng.normal(0, 0.0009, self.SESSION_MINUTES)
            close = price * np.exp(np.cumsum(rets))
            open_ = np.concatenate([[price], close[:-1]])
            wick = np.abs(rng.normal(0, 0.0004, (2, self.SESSION_MINUTES)))
            high = np.maximum(open_, close) * (1 + wick[0])
            low = np.minimum(open_, close) * (1 - wick[1])
            shape = 1 + 2.5 * np.exp(-m / 25) + 1.2 * np.exp(-(374 - m) / 30)
            vol = base_vol * shape * rng.lognormal(0, 0.35, self.SESSION_MINUTES)
            if is_today and today_gap:
                vol *= np.where(m < 30, rng.uniform(2.5, 4.0), 1.4)
            idx = pd.date_range(datetime.combine(day, dtime(9, 15), tzinfo=IST),
                                periods=self.SESSION_MINUTES, freq="1min")
            frames.append(pd.DataFrame({"open": open_, "high": high, "low": low,
                                        "close": close, "volume": vol.round()}, index=idx))
            price = close[-1]
        return pd.concat(frames).round({"open": 2, "high": 2, "low": 2, "close": 2})

    def get_candles(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        if symbol not in self.bars:
            raise DataSourceError(f"Unknown symbol {symbol}")
        df = self.bars[symbol]
        df = df[df.index >= pd.Timestamp(start)]
        return only_closed_bars(df, min(end, self.clock.now())).copy()

    def get_quotes(self, symbols: list[str]) -> pd.DataFrame:
        now = self.clock.now()
        rows = {}
        for sym in symbols:
            df = only_closed_bars(self.bars[sym], now)
            today = df[df.index.date == self.today]
            prev = self.prev_close[sym]
            rows[sym] = {"ltp": today["close"].iloc[-1] if len(today) else prev,
                         "prev_close": prev, "volume": today["volume"].sum()}
        return pd.DataFrame.from_dict(rows, orient="index")

    def get_ltp(self, symbol: str) -> float:
        return float(self.get_quotes([symbol]).loc[symbol, "ltp"])


# --------------------------------------------------------------------------- #
# Angel One SmartAPI adapters (template - test with paper mode first)
# --------------------------------------------------------------------------- #
class AngelOneSession:
    SCRIP_MASTER_URL = ("https://margincalculator.angelbroking.com/"
                        "OpenAPI_File/files/OpenAPIScripMaster.json")

    def __init__(self, api_key: str, client_id: str, pin: str, totp_secret: str):
        try:
            import pyotp
            import requests
            from SmartApi import SmartConnect
        except ImportError as exc:
            raise SystemExit("pip install smartapi-python pyotp requests") from exc
        self.api = SmartConnect(api_key=api_key)
        session = self.api.generateSession(client_id, pin, pyotp.TOTP(totp_secret).now())
        if not session or not session.get("status"):
            raise DataSourceError(f"Angel login failed: {session}")
        master = requests.get(self.SCRIP_MASTER_URL, timeout=60).json()
        self.tokens = {row["symbol"][:-3]: row["token"] for row in master
                       if row.get("exch_seg") == "NSE" and row.get("symbol", "").endswith("-EQ")}
        log.info("Angel One login OK, %d NSE-EQ tokens loaded", len(self.tokens))

    def token(self, symbol: str) -> str:
        if symbol not in self.tokens:
            raise DataSourceError(f"No Angel token for {symbol}")
        return self.tokens[symbol]


class AngelOneDataSource(DataSource):
    RATE_LIMIT_SLEEP = 0.35  # historical API allows ~3 req/sec

    def __init__(self, session: AngelOneSession):
        self.s = session

    def get_quotes(self, symbols: list[str]) -> pd.DataFrame:
        tok_to_sym = {self.s.token(sym): sym for sym in symbols if sym in self.s.tokens}
        resp = self.s.api.getMarketData("FULL", {"NSE": list(tok_to_sym)})
        if not resp or not resp.get("status"):
            raise DataSourceError(f"getMarketData failed: {resp}")
        rows = {}
        for item in resp["data"]["fetched"]:
            sym = tok_to_sym.get(str(item["symbolToken"]))
            if sym:
                rows[sym] = {"ltp": float(item["ltp"]), "prev_close": float(item["close"]),
                             "volume": float(item["tradeVolume"])}
        return pd.DataFrame.from_dict(rows, orient="index")

    def get_candles(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        params = {"exchange": "NSE", "symboltoken": self.s.token(symbol),
                  "interval": "ONE_MINUTE",
                  "fromdate": start.strftime("%Y-%m-%d %H:%M"),
                  "todate": end.strftime("%Y-%m-%d %H:%M")}
        resp = self.s.api.getCandleData(params)
        time.sleep(self.RATE_LIMIT_SLEEP)
        if not resp or not resp.get("status"):
            raise DataSourceError(f"getCandleData {symbol} failed: {resp}")
        data = resp.get("data") or []
        df = pd.DataFrame(data, columns=["ts", "open", "high", "low", "close", "volume"])
        if df.empty:
            return df.set_index("ts")
        df.index = pd.to_datetime(df.pop("ts")).dt.tz_convert(IST)
        df = df[df.index >= pd.Timestamp(start)].astype(float)
        return only_closed_bars(df, min(end, datetime.now(IST)))

    def get_ltp(self, symbol: str) -> float:
        resp = self.s.api.ltpData("NSE", f"{symbol}-EQ", self.s.token(symbol))
        if not resp or not resp.get("status"):
            raise DataSourceError(f"ltpData {symbol} failed: {resp}")
        return float(resp["data"]["ltp"])


class AngelOneBroker(Broker):
    def __init__(self, session: AngelOneSession):
        self.s = session

    def place_order(self, symbol: str, side: str, qty: int, ref_price: float) -> OrderResult:
        params = {"variety": "NORMAL", "tradingsymbol": f"{symbol}-EQ",
                  "symboltoken": self.s.token(symbol), "transactiontype": side,
                  "exchange": "NSE", "ordertype": "MARKET", "producttype": "INTRADAY",
                  "duration": "DAY", "quantity": str(qty)}
        order_id = self.s.api.placeOrder(params)
        if not order_id:
            raise OrderError(f"Order rejected: {params}")
        fill = ref_price
        time.sleep(1)
        try:  # read actual average fill price from the order book
            book = self.s.api.orderBook().get("data") or []
            for o in book:
                if str(o.get("orderid")) == str(order_id):
                    if o.get("status") == "rejected":
                        raise OrderError(f"{symbol} rejected: {o.get('text')}")
                    if float(o.get("averageprice") or 0) > 0:
                        fill = float(o["averageprice"])
        except OrderError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not read fill price for %s: %s", order_id, exc)
        return OrderResult(str(order_id), fill)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main() -> None:
    parser = argparse.ArgumentParser(description="VWAP + RSI 5-min intraday algo (NSE cash)")
    parser.add_argument("--mode", choices=["mock", "angel"], default="mock")
    parser.add_argument("--live", action="store_true", help="place REAL orders (angel mode)")
    parser.add_argument("--date", default=None, help="mock trading date YYYY-MM-DD")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    cfg = StrategyConfig()

    if args.mode == "mock":
        day = date.fromisoformat(args.date) if args.date else datetime.now(IST).date()
        clock: Clock = SimClock(datetime.combine(day, dtime(9, 0), tzinfo=IST))
        cfg.poll_seconds = 30  # coarser polling keeps the simulated day fast
        cfg.trade_log_path = "trades_mock.csv"
        data: DataSource = MockDataSource(cfg.universe, clock, history_days=cfg.history_days + 1)
        broker: Broker = PaperBroker()
    else:
        session = AngelOneSession(os.environ["ANGEL_API_KEY"], os.environ["ANGEL_CLIENT_ID"],
                                  os.environ.get("ANGEL_PIN") or os.environ["ANGEL_PASSWORD"], os.environ["ANGEL_TOTP_SECRET"])
        clock = RealClock()
        data = AngelOneDataSource(session)
        broker = AngelOneBroker(session) if args.live else PaperBroker()
        log.info("Order mode: %s", "LIVE" if args.live else "PAPER")

    IntradayAlgo(cfg, data, broker, clock).run()


if __name__ == "__main__":
    main()
