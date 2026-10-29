#!/usr/bin/env python3
"""Final-settings Sector Pullback on 3 stock universes, with/without an emergency SL.
Settings: Nifty intraday filter (just above/below prev close + open), sector filter (top 2, median),
Nifty 20-day EMA regime, 5 candidates & 5 trades a day, pullback entry after 10:15, exit 15:00.
Daily liquidity filters (price >= 50, 20d median volume >= 2 lakh, turnover >= 5 Cr) apply every day.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import pandas as pd

from backtest import Market, Params, load_csv, run, stats, symbol_from_path

ap = argparse.ArgumentParser()
ap.add_argument("--data-dir", required=True)
ap.add_argument("--out", required=True)
args = ap.parse_args()
HERE = Path(__file__).resolve().parent
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)
read = lambda f: {x.strip().upper() for x in (HERE / f).read_text().splitlines() if x.strip()}  # noqa: E731
n500 = read("nifty500.txt")
sm = pd.read_csv(HERE / "sector_map.csv")
sectors = dict(zip(sm["symbol"].str.upper(), sm["sector"]))
stocks, nifty = {}, None
for pth in sorted(Path(args.data_dir).iterdir()):
    if not pth.name.endswith((".csv", ".csv.gz")):
        continue
    sym = symbol_from_path(pth)
    if sym == "NIFTY":
        nifty = load_csv(pth)
    elif sym in n500:
        stocks[sym] = load_csv(pth)
base = Params(decision_time="10:15", rr=2.0, breakeven=False, top_sectors=2, nifty_regime=20,
              exit_mode="eod", max_candidates=5, max_trades_day=5, market_min_pct=0.0)
m = Market(stocks, nifty, sectors, base.ema_period)
days = m.days
cut = int(len(days) * 2 / 3)
periods = {"IS": days[:cut], "OOS": days[cut:]}
months = {k: len(v) / 21 for k, v in periods.items()}
universes = {
    "182 list (fixed, non-F&O)": read("nifty500_selected182.txt") & set(stocks),
    "All non-F&O in Nifty 500 (289)": read("nifty500_nonfno.txt") & set(stocks),
    "All Nifty 500 (F&O stocks in cash too)": None,
}
rows, trades = [], []
for uname, tradable in universes.items():
    m.tradable = tradable
    for dsl in (0.0, 3.0, 2.0):
        p = replace(base, disaster_sl_pct=dsl)
        r = {"universe": uname, "emergency_SL": "none" if not dsl else f"{dsl:g}%",
             "stocks": len(tradable) if tradable else len(stocks)}
        for per, dd in periods.items():
            t = run(m, p, dd)
            st = stats(t)
            r.update({f"{per}_per_month": round(st["trades"] / months[per], 1), f"{per}_net": st["net"],
                      f"{per}_avg": round(st["net"] / max(st["trades"], 1)), f"{per}_win": st["win_rate"],
                      f"{per}_pf": st["pf"], f"{per}_dd": st["max_dd"],
                      f"{per}_worst": round(t["net"].min()) if len(t) else 0,
                      f"{per}_L": round(t.loc[t["side"] == "LONG", "net"].sum()) if len(t) else 0,
                      f"{per}_S": round(t.loc[t["side"] == "SHORT", "net"].sum()) if len(t) else 0})
            if len(t):
                trades.append(t.assign(universe=uname, emergency_SL=dsl, period=per))
        rows.append(r)
        print(r, flush=True)
res = pd.DataFrame(rows)
res.to_csv(out / "universe_test.csv", index=False)
tr = pd.concat(trades)
tr.to_csv(out / "trades.csv", index=False)
tr["month"] = pd.to_datetime(tr["date"]).dt.to_period("M").astype(str)
pm = tr.groupby(["universe", "emergency_SL", "month"])["net"].sum().groupby(level=[0, 1]).apply(
    lambda s: round((s > 0).mean(), 2)).rename("months_in_profit")
md = ["# Sector Pullback - final settings on 3 universes", "",
      f"IS {days[0]} -> {days[cut-1]} (Nifty +30%) | OOS {days[cut]} -> {days[-1]} (Nifty -12%)",
      "Rs 1,000 risk sizing (to pullback stop), max Rs 2 L per trade, after charges.", "",
      res[["universe", "emergency_SL", "stocks", "IS_per_month", "IS_net", "IS_pf", "OOS_per_month", "OOS_net",
           "OOS_pf", "OOS_dd"]].to_markdown(index=False), "",
      "## Long / short, worst trade", "",
      res[["universe", "emergency_SL", "IS_L", "IS_S", "OOS_L", "OOS_S", "IS_worst", "OOS_worst",
           "IS_win", "OOS_win"]].to_markdown(index=False), "",
      "## Share of months in profit", "", pm.to_markdown()]
(out / "summary.md").write_text("\n".join(md))
print("\n".join(md))
