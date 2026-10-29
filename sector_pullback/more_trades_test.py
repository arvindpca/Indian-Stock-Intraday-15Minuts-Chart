#!/usr/bin/env python3
"""Sector Pullback: how to get MORE trades without killing the edge.
Base = Nifty intraday filter + sector filter + Nifty 20-day EMA regime + no SL/TP (hold to 15:00).
Each variant loosens one rule; then combinations. Reported for IS (Nifty +30%) and OOS (Nifty -12%).
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import pandas as pd

from backtest import Market, Params, load_csv, run, stats, symbol_from_path

ap = argparse.ArgumentParser()
ap.add_argument("--data-dir", required=True)
ap.add_argument("--symbols-file", required=True)
ap.add_argument("--trade-symbols-file", required=True)
ap.add_argument("--sector-map", default=str(Path(__file__).with_name("sector_map.csv")))
ap.add_argument("--out", required=True)
args = ap.parse_args()
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)
wanted = {x.strip().upper() for x in Path(args.symbols_file).read_text().splitlines() if x.strip()}
sm = pd.read_csv(args.sector_map)
sectors = dict(zip(sm["symbol"].str.upper(), sm["sector"]))
stocks, nifty = {}, None
for pth in sorted(Path(args.data_dir).iterdir()):
    if not pth.name.endswith((".csv", ".csv.gz")):
        continue
    sym = symbol_from_path(pth)
    if sym == "NIFTY":
        nifty = load_csv(pth)
    elif sym in wanted:
        stocks[sym] = load_csv(pth)
base = Params(decision_time="10:15", rr=2.0, breakeven=False, top_sectors=2, nifty_regime=20, exit_mode="eod")
m = Market(stocks, nifty, sectors, base.ema_period)
m.tradable = {x.strip().upper() for x in Path(args.trade_symbols_file).read_text().splitlines() if x.strip()} & set(stocks)
days = m.days
cut = int(len(days) * 2 / 3)
periods = {"IS": days[:cut], "OOS": days[cut:]}
months = {k: len(v) / 21 for k, v in periods.items()}

A = dict(max_candidates=5, max_trades_day=5)
B = dict(top_sectors=3)
C = dict(stock_min_pct=0.5, stock_max_pct=4.0)
D = dict(min_rvol=1.2)
E = dict(market_min_pct=0.0)
F = dict(last_entry="14:00")
G = dict(decision_time="09:45")
import os
ONLY = os.environ.get("ONLY_COMBO") == "1"
variants = {
    "BASE (20-day trend, no SL/TP)": {},
    "A: 5 stocks & 5 trades a day (was 3)": A,
    "E: Nifty just above/below prev close (was 0.2%)": E,
    "A+E": {**A, **E},
    "A+E, 7 stocks & 7 trades a day": {**E, "max_candidates": 7, "max_trades_day": 7},
} if ONLY else {
    "BASE (20-day trend, no SL/TP)": {},
    "A: 5 stocks & 5 trades a day (was 3)": A,
    "B: top 3 sectors (was 2)": B,
    "C: stock move 0.5%-4% (was 1%-3.5%)": C,
    "D: rel. volume 1.2x (was 1.5x)": D,
    "E: Nifty just above/below prev close (was 0.2%)": E,
    "F: entry till 14:00 (was 13:30)": F,
    "G: start 09:45 (was 10:15)": G,
    "A+B": {**A, **B},
    "A+B+C": {**A, **B, **C},
    "A+B+C+D": {**A, **B, **C, **D},
    "A+B+C+D+E": {**A, **B, **C, **D, **E},
    "ALL (A..F)": {**A, **B, **C, **D, **E, **F},
}
rows = []
for name, kw in variants.items():
    p = replace(base, **kw)
    r = {"variant": name}
    for per, dd in periods.items():
        t = run(m, p, dd)
        st = stats(t)
        r.update({f"{per}_trades": st["trades"], f"{per}_per_month": round(st["trades"] / months[per], 1),
                  f"{per}_net": st["net"], f"{per}_avg": round(st["net"] / max(st["trades"], 1)),
                  f"{per}_win": st["win_rate"], f"{per}_pf": st["pf"], f"{per}_dd": st["max_dd"]})
        if not t.empty:
            r[f"{per}_worst"] = round(t["net"].min())
            r[f"{per}_L"] = round(t.loc[t["side"] == "LONG", "net"].sum())
            r[f"{per}_S"] = round(t.loc[t["side"] == "SHORT", "net"].sum())
    rows.append(r)
    print(name, {k: v for k, v in r.items() if k.endswith(("trades", "net", "pf"))}, flush=True)
res = pd.DataFrame(rows)
res.to_csv(out / "more_trades.csv", index=False)
md = ["# Sector Pullback - more trades test", "",
      f"IS {days[0]} -> {days[cut-1]} | OOS {days[cut]} -> {days[-1]} | Rs 1,000 risk sizing, after charges", "",
      res[["variant", "IS_per_month", "IS_net", "IS_avg", "IS_pf", "OOS_per_month", "OOS_net", "OOS_avg",
           "OOS_pf", "OOS_dd"]].to_markdown(index=False), "",
      "## Long / short and worst trade", "",
      res[["variant", "IS_L", "IS_S", "OOS_L", "OOS_S", "IS_worst", "OOS_worst"]].to_markdown(index=False)]
(out / "summary.md").write_text("\n".join(md))
print("\n".join(md))
