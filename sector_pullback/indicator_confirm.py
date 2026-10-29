#!/usr/bin/env python3
"""Proper re-run of the filters that won the screening (filter inside the entry rule, so other
candidates can fill the slot): Nifty 5-min above/below its EMA20 at entry, and price on our side of
today's volume POC. Rs 50,000 per trade, 289 non-F&O, skip Monday."""
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


def sc(end, step, last):
    t = [f"{x // 60:02d}:{x % 60:02d}" for x in range(615, int(end[:2]) * 60 + int(end[3:]) + 1, step)]
    return replace(final, decision_time=t[0], extra_scans=tuple(t[1:]), last_entry=last, confirm_time="10:15")


S5 = sc("14:30", 5, "14:30")
S5e = sc("12:30", 5, "13:00")
NE = dict(nifty_ema_entry=True)
NP = dict(nifty_ema_entry=True, poc_entry=True)
versions = {
    "FINAL": final,
    "FINAL + Nifty EMA": replace(final, **NE),
    "FINAL + Nifty EMA + POC": replace(final, **NP),
    "3 scans + Nifty EMA + POC": replace(final, extra_scans=("11:15", "12:15"), **NP),
    "Every 5 min (10:15-14:30, confirmed days)": S5,
    "Every 5 min + Nifty EMA": replace(S5, **NE),
    "Every 5 min + Nifty EMA + POC": replace(S5, **NP),
    "Every 5 min till 12:30 + Nifty EMA + POC": replace(S5e, **NP),
}
brokerages = {"Rs 20/order": {"pct": 0.10, "cap": 20.0, "min": 5.0},
              "Zero brokerage": {"pct": 0.0, "cap": 0.0, "min": 0.0}}
rows, monthly = [], {}
for vname, p in versions.items():
    for bname, bset in brokerages.items():
        bt.BROKERAGE.update(bset)
        t = run(m, p, days)
        st = stats(t)
        t["month"] = pd.to_datetime(t["date"]).dt.to_period("M").astype(str)
        mm = t.groupby("month")["net"].sum()
        y1 = t.loc[pd.to_datetime(t["date"]) < start + pd.DateOffset(years=1), "net"].sum()
        rows.append({"version": vname, "brokerage": bname,
                     "days_with_trade_%": round(t["date"].nunique() / len(days) * 100),
                     "trades_per_month": round(st["trades"] / (len(days) / 21), 1), "trades": st["trades"],
                     "net": st["net"], "avg_per_trade": round(st["net"] / max(st["trades"], 1)),
                     "win_%": st["win_rate"], "max_dd": st["max_dd"], "charges": st["charges"],
                     "months_profit": f"{int((mm > 0).sum())}/{len(mm)}", "year1": round(y1),
                     "year2": round(st["net"] - y1)})
        if bname == "Rs 20/order":
            monthly[vname] = mm
            t.to_csv(out / f"trades_{vname.replace(' ', '_').replace('+', 'p').replace('(', '').replace(')', '').replace(',', '').replace(':', '')}.csv", index=False)
        print(rows[-1], flush=True)
    bt._SCAN_CACHE.clear()
res = pd.DataFrame(rows)
res.to_csv(out / "indicator_confirm.csv", index=False)
mon = pd.DataFrame(monthly).fillna(0).round(0)
mon.to_csv(out / "monthly_rs20.csv")
md = ["# Indicator filters - proper re-run", "",
      f"Window {days[0]} -> {days[-1]} ({len(days)} days), Rs 50,000 per trade, 289 non-F&O, skip Monday", ""]
for b in brokerages:
    md += [f"## {b}", "", res[res["brokerage"] == b].drop(columns="brokerage").to_markdown(index=False), ""]
md += ["## Month-wise (Rs 20/order)", "", mon.to_markdown()]
(out / "summary.md").write_text("\n".join(md))
print("\n".join(md))
