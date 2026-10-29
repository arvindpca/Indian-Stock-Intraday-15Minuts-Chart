#!/usr/bin/env python3
"""More trades without loosening filters: extra scan times during the day, and the effect of
lower brokerage. Fixed Rs 50,000 per trade, 289 non-F&O, same window as the FINAL tests."""
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import pandas as pd

import backtest as bt
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
final = Params(decision_time="10:15", rr=2.0, breakeven=False, top_sectors=2, nifty_regime=20,
               exit_mode="eod", max_candidates=5, max_trades_day=5, market_min_pct=0.0,
               fixed_notional=50_000, skip_weekdays=(0,))
m = Market(stocks, nifty, sectors, final.ema_period)
m.tradable = read("nifty500_nonfno.txt") & set(stocks)
start = pd.to_datetime(run(m, final, m.days)["date"]).min()
days = [d for d in m.days if pd.Timestamp(d) >= start]
versions = {
    "FINAL (scan 10:15)": final,
    "Scan 10:15 + 11:15": replace(final, extra_scans=("11:15",)),
    "Scan 10:15 + 11:15 + 12:15": replace(final, extra_scans=("11:15", "12:15")),
    "Scan 10:15 + 11:15 + 12:15, last entry 14:00": replace(final, extra_scans=("11:15", "12:15"), last_entry="14:00"),
    "Scan 10:15 + 11:15 + 12:15 + Monday": replace(final, extra_scans=("11:15", "12:15"), skip_weekdays=()),
    "FINAL + Monday": replace(final, skip_weekdays=()),
    "Nifty 10:15 ignored, 20-day trend only": replace(final, skip_weekdays=(), direction="regime"),
}
brokerages = {"Rs 20/order (now)": {"pct": 0.10, "cap": 20.0, "min": 5.0},
              "Rs 10/order": {"pct": 0.10, "cap": 10.0, "min": 0.0},
              "Zero brokerage": {"pct": 0.0, "cap": 0.0, "min": 0.0}}
rows = []
for bname, bset in brokerages.items():
    bt.BROKERAGE.update(bset)
    for vname, p in versions.items():
        t = run(m, p, days)
        st = stats(t)
        t["month"] = pd.to_datetime(t["date"]).dt.to_period("M")
        mm = t.groupby("month")["net"].sum()
        y1 = t.loc[pd.to_datetime(t["date"]) < start + pd.DateOffset(years=1), "net"].sum()
        rows.append({"brokerage": bname, "version": vname, "days_with_trade_%": round(t["date"].nunique() / len(days) * 100),
                     "trades_per_month": round(st["trades"] / (len(days) / 21), 1), "trades": st["trades"],
                     "net": st["net"], "avg_per_trade": round(st["net"] / max(st["trades"], 1)),
                     "win_%": st["win_rate"], "max_dd": st["max_dd"], "charges": st["charges"],
                     "months_profit": f"{int((mm > 0).sum())}/{len(mm)}", "year1": round(y1), "year2": round(st["net"] - y1)})
        print(rows[-1], flush=True)
res = pd.DataFrame(rows)
res.to_csv(out / "extra_test.csv", index=False)
md = ["# More trades: extra scan times + brokerage", "",
      f"Window {days[0]} -> {days[-1]} ({len(days)} days), fixed Rs 50,000 per trade, 289 non-F&O", ""]
for b in brokerages:
    md += [f"## {b}", "", res[res["brokerage"] == b].drop(columns="brokerage").to_markdown(index=False), ""]
(out / "summary.md").write_text("\n".join(md))
print("\n".join(md))
