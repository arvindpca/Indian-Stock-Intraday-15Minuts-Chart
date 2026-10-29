#!/usr/bin/env python3
"""Sector Pullback (Nifty + sector filter ON): does a bigger Nifty trend filter fix the
'only works on the short side' problem?  Regime = Nifty prev close vs its 20/50-day EMA:
above -> only LONG, below -> only SHORT. Tested with SL+2R and with no SL/TP (hold to 15:00).
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import numpy as np
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
base = Params(decision_time="10:15", rr=2.0, breakeven=False, top_sectors=2)
m = Market(stocks, nifty, sectors, base.ema_period)
m.tradable = {x.strip().upper() for x in Path(args.trade_symbols_file).read_text().splitlines() if x.strip()} & set(stocks)
days = m.days
cut = int(len(days) * 2 / 3)
periods = {"IS": days[:cut], "OOS": days[cut:]}
nc = m.nifty_daily_close
ret = lambda dd: f"{(nc.loc[dd[-1]] / nc.loc[dd[0]] - 1) * 100:+.0f}%"  # noqa: E731
print(f"IS {days[0]}..{days[cut-1]} Nifty {ret(periods['IS'])} | OOS {days[cut]}..{days[-1]} Nifty {ret(periods['OOS'])}")

rows, trades = [], []
for exit_mode, elabel in (("sltp", "SL + 1:2 target"), ("eod", "No SL/TP")):
    for reg, rlabel in ((0, "no regime filter (old)"), (20, "Nifty vs 20-day EMA"), (50, "Nifty vs 50-day EMA")):
        p = replace(base, exit_mode=exit_mode, nifty_regime=reg)
        r = {"exit": elabel, "regime": rlabel}
        for per, dd in periods.items():
            t = run(m, p, dd)
            st = stats(t)
            r.update({f"{per}_trades": st["trades"], f"{per}_net": st["net"], f"{per}_win": st["win_rate"],
                      f"{per}_pf": st["pf"], f"{per}_dd": st["max_dd"]})
            for sd in ("LONG", "SHORT"):
                x = t[t["side"] == sd] if not t.empty else t
                r[f"{per}_{sd}_n"] = len(x)
                r[f"{per}_{sd}_net"] = round(x["net"].sum()) if len(x) else 0
            if not t.empty:
                t = t.assign(exit_mode=exit_mode, regime=reg, period=per)
                trades.append(t)
        rows.append(r)
        print(r, flush=True)
res = pd.DataFrame(rows)
res.to_csv(out / "regime_test.csv", index=False)
tr = pd.concat(trades)
tr.to_csv(out / "trades.csv", index=False)
tr["month"] = pd.to_datetime(tr["date"]).dt.to_period("M").astype(str)
mon = tr.pivot_table(index="month", columns=["exit_mode", "regime"], values="net", aggfunc="sum").round(0)
pos = (mon > 0).mean().round(2)
md = ["# Sector Pullback - Nifty regime filter test", "",
      f"IS {days[0]} -> {days[cut-1]} (Nifty {ret(periods['IS'])}) | OOS {days[cut]} -> {days[-1]} (Nifty {ret(periods['OOS'])})",
      "Nifty intraday filter + sector filter ON in all rows. Rs 1,000 risk sizing, after charges.", "",
      "## Total", "",
      res[["exit", "regime", "IS_trades", "IS_net", "IS_win", "IS_pf", "OOS_trades", "OOS_net", "OOS_win",
           "OOS_pf", "OOS_dd"]].to_markdown(index=False), "",
      "## Long vs short", "",
      res[["exit", "regime", "IS_LONG_n", "IS_LONG_net", "IS_SHORT_n", "IS_SHORT_net",
           "OOS_LONG_n", "OOS_LONG_net", "OOS_SHORT_n", "OOS_SHORT_net"]].to_markdown(index=False), "",
      "## Share of months in profit", "", pos.to_markdown(), "",
      "## Monthly P&L", "", mon.to_markdown()]
(out / "summary.md").write_text("\n".join(md))
print("\n".join(md[:20]))
