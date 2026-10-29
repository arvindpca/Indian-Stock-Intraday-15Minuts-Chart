#!/usr/bin/env python3
"""Judge candidate versions against intraday benchmarks:
avg profit per trade (% of trade value, after charges), profit factor, % profitable months,
max drawdown, capital needed (worst day's open positions x Rs 50,000) and yearly return on that capital.
Both years shown separately. Rs 20/order and zero brokerage."""
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
import os
AMT = int(os.environ.get("AMT", "50000"))
final = Params(decision_time="10:15", rr=2.0, breakeven=False, top_sectors=2, nifty_regime=20,
               exit_mode="eod", max_candidates=5, max_trades_day=5, market_min_pct=0.0,
               fixed_notional=AMT, skip_weekdays=(0,))
m = Market(stocks, nifty, sectors, final.ema_period)
m.tradable = read("nifty500_nonfno.txt") & set(stocks)
start = pd.to_datetime(run(m, final, m.days)["date"]).min()
days = [d for d in m.days if pd.Timestamp(d) >= start]
tl = [f"{x // 60:02d}:{x % 60:02d}" for x in range(615, 871, 5)]
S5 = replace(final, decision_time=tl[0], extra_scans=tuple(tl[1:]), last_entry="14:30", confirm_time="10:15")
NP = dict(nifty_ema_entry=True, poc_entry=True)
ONLY2 = os.environ.get("ONLY2") == "1"
V = {
    "A. FINAL": final,
    "B. FINAL, max 2 trades/day": replace(final, max_trades_day=2),
    "E. FINAL + NiftyEMA + POC": replace(final, **NP),
    "E2. FINAL + NiftyEMA + POC, max 2/day": replace(final, max_trades_day=2, **NP),
    "F2. FINAL + NiftyEMA + POC + ATR>=3%, max 2/day": replace(final, min_atr_pct=3.0, max_trades_day=2, **NP),
    "G2. 5-min + NiftyEMA + POC, max 2/day": replace(S5, max_trades_day=2, **NP),
} if ONLY2 else {
    "A. FINAL": final,
    "B. FINAL, max 2 trades/day": replace(final, max_trades_day=2),
    "C. FINAL + ATR>=3%": replace(final, min_atr_pct=3.0),
    "D. FINAL + ATR>=3.5%": replace(final, min_atr_pct=3.5),
    "E. FINAL + NiftyEMA + POC": replace(final, **NP),
    "F. FINAL + NiftyEMA + POC + ATR>=3%": replace(final, min_atr_pct=3.0, **NP),
    "G. 5-min + NiftyEMA + POC": replace(S5, **NP),
    "H. 5-min + NiftyEMA + POC + ATR>=3%": replace(S5, min_atr_pct=3.0, **NP),
    "I. 5-min + NiftyEMA + POC + ATR>=3%, max 3/day": replace(S5, min_atr_pct=3.0, max_trades_day=3, **NP),
}
B = {"Rs 20/order": {"pct": 0.10, "cap": 20.0, "min": 5.0},
     "Zero brokerage": {"pct": 0.0, "cap": 0.0, "min": 0.0}}
years = len(days) / 245
rows = []
for name, p in V.items():
    for bname, bs in B.items():
        bt.BROKERAGE.update(bs)
        t = run(m, p, days)
        st = stats(t)
        t["month"] = pd.to_datetime(t["date"]).dt.to_period("M")
        mm = t.groupby("month")["net"].sum()
        cap = int(t.groupby("date").size().max()) * AMT
        y1 = t.loc[pd.to_datetime(t["date"]) < start + pd.DateOffset(years=1), "net"].sum()
        yr = st["net"] / years
        rows.append({"version": name, "brokerage": bname, "trades/month": round(st["trades"] / (len(days) / 21), 1),
                     "avg/trade %": round(st["net"] / max(st["trades"], 1) / AMT * 100, 2),
                     "PF": st["pf"], "win %": st["win_rate"],
                     "profit months %": round((mm > 0).mean() * 100),
                     "capital needed": cap, "max DD % of capital": round(st["max_dd"] / cap * 100, 1),
                     "yearly return %": round(yr / cap * 100, 1),
                     "Y1 Rs": round(y1), "Y2 Rs": round(st["net"] - y1), "total Rs": st["net"]})
        if bname == "Rs 20/order":
            safe = name.split(".")[0]
            t.to_csv(out / f"trades_{safe}.csv", index=False)
            g = t.groupby("month").agg(trades=("net", "size"), wins=("net", lambda x: int((x > 0).sum())),
                                       net=("net", "sum"))
            g["net"] = g["net"].round(0)
            g.to_csv(out / f"monthly_{safe}.csv")
        print(rows[-1], flush=True)
    bt._SCAN_CACHE.clear()
res = pd.DataFrame(rows)
res.to_csv(out / "benchmark_test.csv", index=False)
md = ["# Candidates vs intraday benchmarks", "",
      f"Window {days[0]} -> {days[-1]} ({len(days)} days ~ {years:.1f} years), Rs {AMT:,} per trade, 289 non-F&O, skip Monday",
      "Benchmarks: avg/trade >= 0.2% ok, >= 0.4% good | PF >= 1.3 ok, >= 1.7 good | profit months >= 60% | "
      "yearly return >= 12% ok, >= 25% good | max DD <= 20% of capital", ""]
for b in B:
    md += [f"## {b}", "", res[res["brokerage"] == b].drop(columns="brokerage").to_markdown(index=False), ""]
(out / "summary.md").write_text("\n".join(md))
print("\n".join(md))
