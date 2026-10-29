#!/usr/bin/env python3
"""Fixes from the loss analysis, tested on the final version (289 non-F&O, no SL/TP).
ATR thresholds 2.5 / 3.0 / 3.5 / 4.0 are all shown, so we can see if the effect is smooth (real)
or only works at one lucky number (overfit).
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
m.tradable = read("nifty500_nonfno.txt") & set(stocks)
days = m.days
cut = int(len(days) * 2 / 3)
periods = {"IS": days[:cut], "OOS": days[cut:]}
months = {k: len(v) / 21 for k, v in periods.items()}
MON = (0,)
variants = {
    "BASE (final version)": {},
    "ATR >= 2.5%": dict(min_atr_pct=2.5),
    "ATR >= 3.0%": dict(min_atr_pct=3.0),
    "ATR >= 3.5%": dict(min_atr_pct=3.5),
    "ATR >= 4.0%": dict(min_atr_pct=4.0),
    "Rank by ATR (no minimum)": dict(rank_by="atr"),
    "Skip Monday": dict(skip_weekdays=MON),
    "ATR >= 3.0% + skip Monday": dict(min_atr_pct=3.0, skip_weekdays=MON),
    "ATR >= 3.5% + skip Monday": dict(min_atr_pct=3.5, skip_weekdays=MON),
}
rows, trades = [], []
for name, kw in variants.items():
    p = replace(base, **kw)
    r = {"variant": name}
    for per, dd in periods.items():
        t = run(m, p, dd)
        st = stats(t)
        r.update({f"{per}_per_month": round(st["trades"] / months[per], 1), f"{per}_net": st["net"],
                  f"{per}_avg": round(st["net"] / max(st["trades"], 1)), f"{per}_win": st["win_rate"],
                  f"{per}_pf": st["pf"], f"{per}_dd": st["max_dd"],
                  f"{per}_L": round(t.loc[t["side"] == "LONG", "net"].sum()) if len(t) else 0,
                  f"{per}_S": round(t.loc[t["side"] == "SHORT", "net"].sum()) if len(t) else 0})
        if len(t):
            t = t.assign(variant=name, period=per)
            t["month"] = pd.to_datetime(t["date"]).dt.to_period("M").astype(str)
            trades.append(t)
            mm = t.groupby("month")["net"].sum()
            r[f"{per}_months_profit"] = round((mm > 0).mean(), 2)
    rows.append(r)
    print(name, r, flush=True)
res = pd.DataFrame(rows)
res.to_csv(out / "fix_test.csv", index=False)
pd.concat(trades).to_csv(out / "trades.csv", index=False)
md = ["# Sector Pullback - fixes from loss analysis (289 non-F&O, no SL/TP)", "",
      f"IS {days[0]} -> {days[cut-1]} (Nifty +30%) | OOS {days[cut]} -> {days[-1]} (Nifty -12%)", "",
      res[["variant", "IS_per_month", "IS_net", "IS_avg", "IS_pf", "IS_dd", "OOS_per_month", "OOS_net",
           "OOS_avg", "OOS_pf", "OOS_dd"]].to_markdown(index=False), "",
      "## Long / short, months in profit", "",
      res[["variant", "IS_L", "IS_S", "OOS_L", "OOS_S", "IS_months_profit", "OOS_months_profit",
           "IS_win", "OOS_win"]].to_markdown(index=False)]
(out / "summary.md").write_text("\n".join(md))
print("\n".join(md))
