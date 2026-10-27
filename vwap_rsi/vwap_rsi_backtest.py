#!/usr/bin/env python3
"""
Backtest for vwap_rsi_algo.py on saved 5-minute CSV data.

Same indicators and same signal function as the live algo, plus:
  * entry at signal-candle close (+ slippage)
  * SL 0.5 % / target 1.0 % checked on later candles' high/low
    (both hit in one candle -> assume SL, conservative; gap through SL -> fill at open)
  * square-off at the open of the 15:15 candle
  * Angel One intraday charges (brokerage, STT, exchange, SEBI, stamp, GST)

Stock selection with 5-min data: the 09:15 candle (closes 09:20) is used for
% change vs previous close and relative volume. First possible signal is the
09:20 candle (closes 09:25), so there is no look-ahead.

Usage
-----
  # one or more CSV files (datetime,open,high,low,close,volume)
  python vwap_rsi_backtest.py --files data/RELIANCE_5m.csv --no-filter
  # a folder of <SYMBOL>.csv / <SYMBOL>.csv.gz / <SYMBOL>_5m.csv files
  python vwap_rsi_backtest.py --data-dir research_data/raw/5m --nifty50-only
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from datetime import time as dtime
from pathlib import Path

import numpy as np
import pandas as pd

from vwap_rsi_algo import NIFTY50, IntradayAlgo, StrategyConfig, add_indicators


@dataclass
class Costs:
    brokerage_pct: float = 0.10
    brokerage_cap: float = 20.0
    brokerage_min: float = 5.0
    stt_sell_pct: float = 0.025
    exchange_pct: float = 0.0030699
    sebi_per_crore: float = 10.0
    stamp_buy_pct: float = 0.003
    gst_pct: float = 18.0
    slippage_pct: float = 0.03

    def charges(self, buy_value: float, sell_value: float) -> float:
        def brok(v: float) -> float:
            return max(self.brokerage_min, min(self.brokerage_cap, v * self.brokerage_pct / 100))
        b = brok(buy_value) + brok(sell_value)
        turnover = buy_value + sell_value
        exch = turnover * self.exchange_pct / 100
        sebi = turnover * self.sebi_per_crore / 1e7
        return (b + sell_value * self.stt_sell_pct / 100 + exch + sebi
                + buy_value * self.stamp_buy_pct / 100 + (b + exch + sebi) * self.gst_pct / 100)


def load_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    ts_col = next(c for c in df.columns if c.lower() in ("datetime", "timestamp", "date", "ts"))
    idx = pd.to_datetime(df[ts_col])
    if idx.dt.tz is not None:
        idx = idx.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    df = df.drop(columns=[ts_col])
    df.columns = [c.lower() for c in df.columns]
    df.index = idx
    df = df[["open", "high", "low", "close", "volume"]].astype(float).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    t = df.index.time
    df = df[(t >= dtime(9, 15)) & (t < dtime(15, 30))]
    # closing-auction prints after 15:15 often have 0 volume - drop them
    t = df.index.time
    df = df[~((t >= dtime(15, 15)) & (df["volume"] == 0))]
    return df


def symbol_from_path(p: Path) -> str:
    name = p.name
    for suf in (".csv.gz", ".csv"):
        name = name.removesuffix(suf)
    return re.sub(r"_5m(_\dy)?$", "", name)


def selection_table(data: dict[str, pd.DataFrame], cfg: StrategyConfig) -> pd.DataFrame:
    """Per symbol per day: first-candle % change vs prev close and relative volume."""
    rows = []
    for sym, df in data.items():
        daily_close = df["close"].groupby(df.index.date).last()
        first = df[df.index.time == dtime(9, 15)]
        first = first.set_index(first.index.date)
        f = pd.DataFrame({"close": first["close"], "vol": first["volume"]})
        f["prev_close"] = daily_close.shift(1).reindex(f.index)
        f["base_vol"] = f["vol"].shift(1).rolling(cfg.history_days, min_periods=3).mean()
        f["change_pct"] = (f["close"] / f["prev_close"] - 1) * 100
        f["rvol"] = f["vol"] / f["base_vol"]
        f["symbol"] = sym
        rows.append(f.dropna())
    return pd.concat(rows) if rows else pd.DataFrame()


def pick_watchlist(sel: pd.DataFrame, cfg: StrategyConfig) -> dict:
    out = {}
    act = sel[sel["rvol"] >= cfg.min_relative_volume]
    for day, g in act.groupby(level=0):
        gain = g[g["change_pct"] > cfg.gainer_threshold_pct].nlargest(cfg.top_n, "change_pct")
        lose = g[g["change_pct"] < cfg.loser_threshold_pct].nsmallest(cfg.top_n, "change_pct")
        out[day] = list(gain["symbol"]) + list(lose["symbol"])
    return out


def simulate_day(sym: str, day_df: pd.DataFrame, cfg: StrategyConfig, costs: Costs) -> list[dict]:
    trades = []
    sl_f, tg_f, slip = cfg.stop_loss_pct / 100, cfg.target_pct / 100, costs.slippage_pct / 100
    times = day_df.index.time
    n, i, count = len(day_df), 1, 0
    while i < n and count < cfg.max_trades_per_symbol:
        if times[i] >= dtime(15, 10):           # candle must close before 15:15
            break
        sig = IntradayAlgo.generate_signal(day_df.iloc[: i + 1], cfg.rsi_buy_level, cfg.rsi_short_level)
        if not sig:
            i += 1
            continue
        long = sig == "BUY"
        c = day_df["close"].iat[i]
        entry = c * (1 + slip) if long else c * (1 - slip)
        sl = entry * (1 - sl_f) if long else entry * (1 + sl_f)
        tg = entry * (1 + tg_f) if long else entry * (1 - tg_f)
        exit_px, reason, j = None, None, i + 1
        while j < n:
            o, h, lo = day_df["open"].iat[j], day_df["high"].iat[j], day_df["low"].iat[j]
            if times[j] >= dtime(15, 15):
                exit_px, reason = o, "TIME_EXIT"
                break
            if long:
                if o <= sl:
                    exit_px, reason = o, "STOP_LOSS"
                elif lo <= sl:
                    exit_px, reason = sl, "STOP_LOSS"
                elif h >= tg:
                    exit_px, reason = max(o, tg), "TARGET"
            else:
                if o >= sl:
                    exit_px, reason = o, "STOP_LOSS"
                elif h >= sl:
                    exit_px, reason = sl, "STOP_LOSS"
                elif lo <= tg:
                    exit_px, reason = min(o, tg), "TARGET"
            if reason:
                break
            j += 1
        if exit_px is None:                      # data ended early
            j = n - 1
            exit_px, reason = day_df["close"].iat[j], "TIME_EXIT"
        exit_px = exit_px * (1 - slip) if long else exit_px * (1 + slip)
        qty = max(1, int(cfg.capital_per_trade // entry))
        gross = (exit_px - entry) * qty * (1 if long else -1)
        buy_v, sell_v = (entry * qty, exit_px * qty) if long else (exit_px * qty, entry * qty)
        ch = costs.charges(buy_v, sell_v)
        trades.append({"symbol": sym, "date": day_df.index[i].date(), "side": "LONG" if long else "SHORT",
                       "signal_candle": day_df.index[i].strftime("%H:%M"),
                       "exit_candle": day_df.index[j].strftime("%H:%M"), "qty": qty,
                       "entry": round(entry, 2), "exit": round(exit_px, 2), "reason": reason,
                       "gross": round(gross, 2), "charges": round(ch, 2), "net": round(gross - ch, 2)})
        count += 1
        i = j + 1
    return trades


def summarize(t: pd.DataFrame, title: str) -> None:
    print(f"\n=== {title} ===")
    if t.empty:
        print("No trades.")
        return
    wins = t[t["net"] > 0]
    loss = t[t["net"] <= 0]
    pf = wins["net"].sum() / abs(loss["net"].sum()) if len(loss) and loss["net"].sum() else np.inf
    eq = t.sort_values(["date", "signal_candle"])["net"].cumsum()
    dd = (eq - eq.cummax()).min()
    days = t["date"].nunique()
    print(f"Trades: {len(t)} on {days} days | Win rate (net>0): {len(wins) / len(t):.1%}")
    print(f"Gross P&L: Rs {t['gross'].sum():,.0f} | Charges: Rs {t['charges'].sum():,.0f} | "
          f"NET P&L: Rs {t['net'].sum():,.0f}")
    print(f"Avg net/trade: Rs {t['net'].mean():,.1f} | Profit factor: {pf:.2f} | Max drawdown: Rs {dd:,.0f}")
    print("Exit reasons:", t["reason"].value_counts().to_dict())
    by = t.groupby("side")["net"].agg(["count", "sum", lambda s: (s > 0).mean()])
    by.columns = ["trades", "net_pnl", "win_rate"]
    print(by.round(2).to_string())
    yr = t.groupby(pd.to_datetime(t["date"]).dt.year)["net"].agg(["count", "sum"])
    yr.columns = ["trades", "net_pnl"]
    print(yr.round(0).to_string())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", nargs="*", default=[])
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--nifty50-only", action="store_true")
    ap.add_argument("--no-filter", action="store_true",
                    help="skip gainer/loser selection: trade every symbol every day")
    ap.add_argument("--out", default="backtest_trades.csv")
    args = ap.parse_args()

    paths = [Path(f) for f in args.files]
    if args.data_dir:
        paths += sorted(p for p in Path(args.data_dir).iterdir()
                        if p.name.endswith((".csv", ".csv.gz")))
    cfg, costs = StrategyConfig(), Costs()

    data = {}
    for p in paths:
        sym = symbol_from_path(p)
        if args.nifty50_only and sym not in NIFTY50:
            continue
        try:
            data[sym] = load_csv(p)
        except Exception as exc:  # noqa: BLE001
            print(f"skip {p.name}: {exc}")
    if not data:
        raise SystemExit("No data loaded.")
    print(f"Loaded {len(data)} symbols: {', '.join(sorted(data))}")

    watch = None if args.no_filter else pick_watchlist(selection_table(data, cfg), cfg)

    all_trades = []
    for sym, df in data.items():
        ind = add_indicators(df, cfg.rsi_period)   # RSI continuous across days, VWAP per day
        for day, day_df in ind.groupby(ind.index.date):
            if len(day_df) < 60:                     # special / half sessions
                continue
            if watch is not None and sym not in watch.get(day, []):
                continue
            all_trades += simulate_day(sym, day_df, cfg, costs)

    t = pd.DataFrame(all_trades)
    first = min(d.index[0] for d in data.values()).date()
    last = max(d.index[-1] for d in data.values()).date()
    mode = "every day, no stock filter" if args.no_filter else "gainer/loser filter ON"
    summarize(t, f"VWAP+RSI 5-min | {first} -> {last} | {mode}")
    if not t.empty:
        t.to_csv(args.out, index=False)
        print(f"\nTrade list saved: {args.out}")


if __name__ == "__main__":
    main()
