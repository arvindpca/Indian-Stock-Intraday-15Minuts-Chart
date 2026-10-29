#!/usr/bin/env python3
"""Which version works best with a FIXED Rs 50,000 per trade?"""
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import pandas as pd

from backtest import Market, Params, load_csv, run, stats, symbol_from_path

ap = argparse.ArgumentParser()
ap.add_argument("--data-dir", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--amount", type=float, default=50_000)
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
A = args.amount
final = Params(decision_time="10:15", rr=2.0, breakeven=False, top_sectors=2, nifty_regime=20,
               exit_mode="eod", max_candidates=5, max_trades_day=5, market_min_pct=0.0,
               fixed_notional=A, skip_weekdays=(0,))
m = Market(stocks, nifty, sectors, final.ema_period)
nonfno = read("nifty500_nonfno.txt") & set(stocks)
days = m.days
first_trade_day = None
variants = {
    "FINAL: non-F&O, skip Monday, hold to 15:00": (final, nonfno),
    "Same, but trade Monday too": (replace(final, skip_weekdays=()), nonfno),
    "FINAL + ATR >= 3%": (replace(final, min_atr_pct=3.0), nonfno),
    "FINAL + ATR >= 3.5%": (replace(final, min_atr_pct=3.5), nonfno),
    "FINAL but with SL + 1:2 target": (replace(final, exit_mode="sltp"), nonfno),
    "FINAL but F&O stocks too": (final, None),
    "Old: no 20-day trend, Nifty 0.2%, 3 trades/day": (replace(final, nifty_regime=0, market_min_pct=0.2,
                                                               max_candidates=3, max_trades_day=3,
                                                               skip_weekdays=()), nonfno),
}
rows, monthly = [], {}
for name, (p, uni) in variants.items():
    m.tradable = uni
    t = run(m, p, days)
    t["month"] = pd.to_datetime(t["date"]).dt.to_period("M").astype(str)
    if first_trade_day is None:
        first_trade_day = pd.to_datetime(t["date"]).min()
    t = t[pd.to_datetime(t["date"]) >= first_trade_day]   # same window for every variant
    st = stats(t)
    mm = t.groupby("month")["net"].sum()
    monthly[name] = mm
    rows.append({"variant": name, "trades": st["trades"], "net": st["net"],
                 "avg_per_trade": round(st["net"] / max(st["trades"], 1)), "win_%": st["win_rate"],
                 "pf": st["pf"], "max_dd": st["max_dd"], "worst_trade": round(t["net"].min()),
                 "best_trade": round(t["net"].max()), "charges": st["charges"],
                 "months_profit": f"{int((mm > 0).sum())}/{len(mm)}",
                 "first_year": round(t.loc[pd.to_datetime(t['date']) < first_trade_day + pd.DateOffset(years=1), "net"].sum()),
                 "second_year": round(t.loc[pd.to_datetime(t['date']) >= first_trade_day + pd.DateOffset(years=1), "net"].sum())})
    print(rows[-1], flush=True)
res = pd.DataFrame(rows)
res.to_csv(out / "fixed_test.csv", index=False)
mon = pd.DataFrame(monthly).fillna(0).round(0)
mon.to_csv(out / "monthly.csv")
md = [f"# Fixed Rs {A:,.0f} per trade - which version is best", "",
      f"Window: {first_trade_day.date()} -> {days[-1]} (same for all), after charges", "",
      res.to_markdown(index=False), "", "## Month-wise (Rs)", "", mon.to_markdown()]
(out / "summary.md").write_text("\n".join(md))
print("\n".join(md))
