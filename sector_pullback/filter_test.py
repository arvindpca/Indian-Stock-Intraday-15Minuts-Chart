#!/usr/bin/env python3
"""Sector Pullback: effect of Nifty trend filter and sector trend filter,
with and without SL/target (no SL/TP = hold till exit time).

Fixed params = the ones chosen on in-sample earlier (decision 10:15, RR 2, no breakeven,
top 2 sectors, median sector strength). Same entries and quantity in both exit modes.
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
base = Params(decision_time="10:15", rr=2.0, breakeven=False, top_sectors=2)
m = Market(stocks, nifty, sectors, base.ema_period)
m.tradable = {x.strip().upper() for x in Path(args.trade_symbols_file).read_text().splitlines() if x.strip()} & set(stocks)
days = m.days
cut = int(len(days) * 2 / 3)
periods = {"IS": days[:cut], "OOS": days[cut:], "ALL": days}
print(f"stocks {len(stocks)}, tradable {len(m.tradable)}, IS {days[0]}..{days[cut-1]}, OOS {days[cut]}..{days[-1]}")

combos = {
    "Nifty ON + Sector ON": dict(use_market=True, use_sector=True),
    "Nifty ON, Sector OFF": dict(use_market=True, use_sector=False),
    "Nifty OFF, Sector ON": dict(use_market=False, use_sector=True),
    "Nifty OFF + Sector OFF": dict(use_market=False, use_sector=False),
}
rows, all_trades = [], []
for exit_mode, label in (("sltp", "SL + 1:2 target"), ("eod", "No SL/TP, hold till 15:00")):
    for name, kw in combos.items():
        p = replace(base, exit_mode=exit_mode, **kw)
        r = {"exit": label, "filters": name}
        for per, dd in periods.items():
            t = run(m, p, dd)
            st = stats(t)
            r.update({f"{per}_trades": st["trades"], f"{per}_net": st["net"], f"{per}_win": st["win_rate"],
                      f"{per}_pf": st["pf"], f"{per}_dd": st["max_dd"]})
            if per == "ALL" and not t.empty:
                t["exit_mode"], t["filters"] = exit_mode, name
                t["period"] = t["date"].isin(set(periods["OOS"])).map({True: "OOS", False: "IS"})
                all_trades.append(t)
        rows.append(r)
        print(r, flush=True)
res = pd.DataFrame(rows)
res.to_csv(out / "filter_test.csv", index=False)
tr = pd.concat(all_trades)
tr.to_csv(out / "trades.csv", index=False)
side = tr.groupby(["exit_mode", "filters", "period", "side"])["net"].agg(["count", "sum"]).round(0)
worst = tr[tr["exit_mode"] == "eod"].groupby("filters")["net"].agg(["min", lambda s: s.quantile(0.05)]).round(0)
worst.columns = ["worst trade", "5% worst"]
md = ["# Sector Pullback - Nifty / Sector filter test", "",
      f"IS {days[0]} -> {days[cut-1]} | OOS {days[cut]} -> {days[-1]} | Rs 1,000 risk sizing, after charges", "",
      res[["exit", "filters", "IS_trades", "IS_net", "IS_win", "OOS_trades", "OOS_net", "OOS_win", "OOS_pf",
           "OOS_dd"]].to_markdown(index=False), "",
      "## Worst single trades (no SL/TP)", "", worst.to_markdown(), "",
      "## By side", "", side.to_markdown()]
(out / "summary.md").write_text("\n".join(md))
print("\n".join(md))
