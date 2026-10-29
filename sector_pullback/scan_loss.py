#!/usr/bin/env python3
"""Loss analysis of the 'scan every 5 minutes 10:00-14:30' version (Rs 50,000 per trade, Rs 20/order).
For every trade: when the scan fired, what the stock / Nifty looked like at that moment, whether the
same direction was already valid at 10:15, its slot in the day, time left to 15:00, MFE / MAE.
A pattern only counts if it shows in BOTH years."""
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import Market, Params, load_csv, run, stats, symbol_from_path, tmin

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
tl = [f"{x // 60:02d}:{x % 60:02d}" for x in range(600, 871, 5)]
p5 = replace(final, decision_time=tl[0], extra_scans=tuple(tl[1:]), last_entry="14:30")
t = run(m, p5, days)
t["year"] = np.where(pd.to_datetime(t["date"]) < start + pd.DateOffset(years=1), "Y1", "Y2")
t["scan_min"] = t["scan"].map(tmin)
t["slot"] = t.groupby("date").cumcount() + 1
t["time_left"] = 900 - t["entry_min"]
t["delay"] = t["entry_min"] - t["scan_min"]
side = np.where(t["side"] == "LONG", 1, -1)

# state at 10:15 for the same day: was today's direction already the same?
n1015 = m.nifty_state(tmin("10:15"))
reg = m.regime(20)
def dir1015(d):
    if d not in n1015.index:
        return 0
    r = n1015.loc[d]
    s = 1 if (r["chg"] >= 0 and r["above_open"]) else (-1 if (r["chg"] <= 0 and r["below_open"]) else 0)
    return s if reg.get(d, 0) == s else 0
t["dir_1015"] = [dir1015(d) for d in t["date"]]
t["same_dir_as_1015"] = np.where(t["dir_1015"] == side, "yes", np.where(t["dir_1015"] == 0, "no clear dir at 10:15", "opposite at 10:15"))

feat = {"chg_at_scan": [], "rvol_at_scan": [], "atr_pct": [], "nifty_at_scan": [], "mfe": [], "mae": []}
for r, sd in zip(t.itertuples(), side):
    snap = m.snapshot(r.scan_min, final.rvol_days)
    row = snap.loc[[r.date]]
    row = row[row["symbol"] == r.symbol]
    feat["chg_at_scan"].append(row["chg"].iat[0] * sd if len(row) else np.nan)
    feat["rvol_at_scan"].append(row["rvol"].iat[0] if len(row) else np.nan)
    feat["atr_pct"].append(row["atr_pct"].iat[0] if len(row) else np.nan)
    ns = m.nifty_state(r.scan_min)
    feat["nifty_at_scan"].append(ns.loc[r.date, "chg"] * sd if r.date in ns.index else np.nan)
    a, b = m.slices[r.symbol][r.date]
    f = m.f[r.symbol]
    mins = f["mins"].values[a:b]
    i0 = int(np.searchsorted(mins, r.entry_min))
    ie = int(np.searchsorted(mins, 900))
    h, l = f["high"].values[a:b][i0:ie], f["low"].values[a:b][i0:ie]
    e = r.entry
    feat["mfe"].append(((h.max() - e) / e * 100) if sd == 1 else ((e - l.min()) / e * 100))
    feat["mae"].append(((l.min() - e) / e * 100) if sd == 1 else ((e - h.max()) / e * 100))
for k, v in feat.items():
    t[k] = v
t.to_csv(out / "trades_5min.csv", index=False)

def piv(col, bins=None, labels=None):
    x = t.assign(b=pd.cut(t[col], bins, labels=labels) if bins is not None else t[col])
    pv = x.pivot_table(index="b", columns="year", values="net", aggfunc=["count", "sum"], observed=False)
    pv.columns = [f"{a}_{b}" for a, b in pv.columns]
    pv["avg"] = (x.groupby("b", observed=False)["net"].mean()).round(0)
    return pv.round(0).to_markdown()

L = ["# Loss analysis - scan every 5 min (10:00-14:30), Rs 50,000/trade, Rs 20/order", "",
     f"Trades {len(t)}, net Rs {t['net'].sum():,.0f}, charges Rs {t['charges'].sum():,.0f}, "
     f"gross before charges Rs {t['gross'].sum():,.0f}", "",
     "## Same direction as 10:15?", "", piv("same_dir_as_1015"), "",
     "## Scan hour", "", piv("scan_min", [599, 659, 719, 779, 839, 900], ["10", "11", "12", "13", "14"]), "",
     "## Slot in the day (1 = first trade)", "", piv("slot"), "",
     "## Stock move at scan time (our direction)", "", piv("chg_at_scan", [-9, 1.5, 2.5, 3.5], ["1-1.5%", "1.5-2.5%", "2.5-3.5%"]), "",
     "## Nifty move at scan time (our direction)", "", piv("nifty_at_scan", [-9, 0.25, 0.5, 1, 9], ["0-0.25%", "0.25-0.5%", "0.5-1%", ">1%"]), "",
     "## Rel. volume at scan", "", piv("rvol_at_scan", [0, 2, 3, 999], ["1.5-2x", "2-3x", ">3x"]), "",
     "## ATR %", "", piv("atr_pct", [0, 2.5, 3.5, 99], ["<2.5%", "2.5-3.5%", ">3.5%"]), "",
     "## Time left to 15:00 after entry", "", piv("time_left", [0, 60, 120, 180, 400], ["<1h", "1-2h", "2-3h", ">3h"]), "",
     "## Side", "", piv("side"), "",
     "## Losers: did they ever go our way?", "",
     f"- losers that never went +0.5%: {(t.loc[t['net'] <= 0, 'mfe'] < 0.5).mean():.0%}",
     f"- losers that went +1% first: {(t.loc[t['net'] <= 0, 'mfe'] >= 1).mean():.0%}",
     f"- charges per trade Rs {t['charges'].mean():.0f}; trades with |gross| < charges: {(t['gross'].abs() < t['charges']).mean():.0%}"]
(out / "summary.md").write_text("\n".join(L))
print("\n".join(L))
