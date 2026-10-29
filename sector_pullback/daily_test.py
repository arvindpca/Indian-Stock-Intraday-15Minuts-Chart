#!/usr/bin/env python3
"""Why not a trade every day? (1) day-by-day funnel of the FINAL version,
(2) a ladder of versions that loosen one rule at a time towards 'a trade every day'.
All with fixed Rs 50,000 per trade, after charges, same window.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import Market, Params, load_csv, run, stats, symbol_from_path, tmin, first_trade

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

# window = days where every stock had enough history (first FINAL trade day onwards)
t0 = run(m, final, m.days)
start = pd.to_datetime(t0["date"]).min()
days = [d for d in m.days if pd.Timestamp(d) >= start]
print(f"window {days[0]} -> {days[-1]}, {len(days)} trading days")

# ---------- 1) funnel
p = final
dmin = tmin(p.decision_time)
snap = m.snapshot(dmin, p.rvol_days)
snap_by_day = dict(tuple(snap.groupby(level=0)))
ns = m.nifty_state(dmin)
reg = m.regime(p.nifty_regime)
fun = []
for day in days:
    r = {"date": day, "stage": None}
    if pd.Timestamp(day).weekday() == 0:
        r["stage"] = "1. Monday (skipped)"
    elif day not in ns.index or pd.isna(ns.loc[day, "chg"]):
        r["stage"] = "0. no Nifty data"
    else:
        row = ns.loc[day]
        nchg = row["chg"]
        side = 1 if (nchg >= 0 and row["above_open"]) else (-1 if (nchg <= 0 and row["below_open"]) else 0)
        if side == 0:
            r["stage"] = "2. Nifty mixed at 10:15 (above close but below open, or reverse)"
        elif reg.get(day, 0) != side:
            r["stage"] = "3. Today's Nifty direction is against the 20-day trend"
        else:
            s = snap_by_day.get(day, pd.DataFrame())
            s = s.dropna(subset=["chg", "rvol", "turnover"])
            s = s[s["turnover"] >= p.min_turnover_cr]
            sec = s[s["sector"] != "UNKNOWN"].groupby("sector")["chg"].agg(["median", "count"])
            sec = sec[sec["count"] >= p.min_sector_stocks]
            sec = sec[sec["median"] > max(nchg, 0)] if side == 1 else sec[sec["median"] < min(nchg, 0)]
            if sec.empty:
                r["stage"] = "4. No sector stronger than Nifty"
            else:
                chosen = list((sec.sort_values("median", ascending=side != 1)).index[:p.top_sectors])
                c = s[s["sector"].isin(chosen) & s["symbol"].isin(m.tradable)]
                c = c[(c["prev_close"] >= p.min_price) & (c["avg_vol"] >= p.min_avg_volume)]
                c = (c[(c["chg"] >= p.stock_min_pct) & (c["chg"] <= p.stock_max_pct) & (c["rvol"] >= p.min_rvol)
                       & (c["close"] > c["vwap"])] if side == 1 else
                     c[(c["chg"] <= -p.stock_min_pct) & (c["chg"] >= -p.stock_max_pct) & (c["rvol"] >= p.min_rvol)
                       & (c["close"] < c["vwap"])])
                if c.empty:
                    r["stage"] = "5. No non-F&O stock in those sectors passes move/volume/VWAP"
                else:
                    trades = [first_trade(m, p, sym, day, side, dmin) for sym in c["symbol"].iloc[:p.max_candidates]]
                    r["stage"] = "7. TRADE" if any(trades) else "6. Stocks found, but no pullback entry by 13:30"
    fun.append(r)
fun = pd.DataFrame(fun)
funnel = fun["stage"].value_counts().sort_index().rename("days").to_frame()
funnel["% of days"] = (funnel["days"] / len(days) * 100).round(1)
fun.to_csv(out / "funnel_days.csv", index=False)

# ---------- 2) ladder towards a trade every day
L = {}
L["1. FINAL"] = final
L["2. + trade Monday too"] = replace(L["1. FINAL"], skip_weekdays=())
L["3. + direction from 20-day trend only (ignore Nifty at 10:15)"] = replace(L["2. + trade Monday too"], direction="regime")
L["4. + top 3 sectors (was 2)"] = replace(L["3. + direction from 20-day trend only (ignore Nifty at 10:15)"], top_sectors=3)
L["5. + stock move 0.5-4%, volume 1.2x"] = replace(L["4. + top 3 sectors (was 2)"], stock_min_pct=0.5, stock_max_pct=4.0, min_rvol=1.2)
L["6. + enter at 10:15 (no pullback wait)"] = replace(L["5. + stock move 0.5-4%, volume 1.2x"], use_pullback=False)
L["7. Every day: only Nifty-direction + top stock, enter 10:15"] = replace(final, skip_weekdays=(), nifty_regime=0, use_sector=False, use_market=True, market_min_pct=0.0, stock_min_pct=0.3, stock_max_pct=5.0, min_rvol=0.0, use_pullback=False)
rows = []
for name, q in L.items():
    t = run(m, q, days)
    st = stats(t)
    d_tr = t["date"].nunique() if len(t) else 0
    yr1 = t[pd.to_datetime(t["date"]) < start + pd.DateOffset(years=1)]["net"].sum() if len(t) else 0
    t["month"] = pd.to_datetime(t["date"]).dt.to_period("M")
    mm = t.groupby("month")["net"].sum()
    rows.append({"version": name, "days_with_trade_%": round(d_tr / len(days) * 100), "trades": st["trades"],
                 "trades_per_month": round(st["trades"] / (len(days) / 21), 1), "net": st["net"],
                 "avg_per_trade": round(st["net"] / max(st["trades"], 1)), "win_%": st["win_rate"],
                 "max_dd": st["max_dd"], "charges": st["charges"], "months_profit": f"{int((mm > 0).sum())}/{len(mm)}",
                 "year1": round(yr1), "year2": round(st["net"] - yr1)})
    print(rows[-1], flush=True)
lad = pd.DataFrame(rows)
lad.to_csv(out / "ladder.csv", index=False)
md = ["# Why not a trade every day?", "", f"Window {days[0]} -> {days[-1]} ({len(days)} trading days), "
      "fixed Rs 50,000 per trade, after charges", "",
      "## 1) FINAL version: where each day stops", "", funnel.to_markdown(), "",
      "## 2) Loosening rules step by step (each row keeps the changes above it, except row 7)", "",
      lad.to_markdown(index=False)]
(out / "summary.md").write_text("\n".join(md))
print("\n".join(md))
