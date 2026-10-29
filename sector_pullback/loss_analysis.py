#!/usr/bin/env python3
"""Loss analysis for the final Sector Pullback version (non-F&O, no SL/TP, exit 15:00).

For every trade we rebuild what was known at entry and what happened after it:
  features : stock move at 10:15, relative volume, Nifty move at 10:15, Nifty distance from its
             20-day EMA, opening gap, entry delay after 10:15, distance from VWAP at entry, ATR %
  path     : best (MFE) and worst (MAE) move after entry, P&L at 11:00 / 12:00 / 13:00 / 14:00
  what-if  : earlier time exits and simple trailing stops (same entry & quantity, after charges)
A pattern is only called real if it shows up in BOTH periods (IS = rising market, OOS = falling).
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import charges, load_csv, prepare

ap = argparse.ArgumentParser()
ap.add_argument("--trades", required=True)
ap.add_argument("--universe", default="All non-F&O")
ap.add_argument("--data-dir", required=True)
ap.add_argument("--out", required=True)
args = ap.parse_args()
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)
SLIP = 0.03 / 100

t = pd.read_csv(args.trades)
t = t[t["universe"].str.startswith(args.universe) & (t["emergency_SL"] == 0)].copy()
t["date"] = pd.to_datetime(t["date"]).dt.date
print("trades", len(t))

data_dir = Path(args.data_dir)


def load(sym):
    for e in (".csv.gz", ".csv"):
        p = data_dir / f"{sym}{e}"
        if p.exists():
            return prepare(load_csv(p), 20)
    return None


nifty = load("NIFTY")
n_daily = nifty.groupby("date")["close"].last()
n_ema = n_daily.ewm(span=20, adjust=False, min_periods=20).mean()
n_dist = ((n_daily - n_ema) / n_ema * 100).shift(1)        # known before the day starts
n_1015 = nifty[nifty["mins"] == 610].set_index("date")
n_chg = (n_1015["close"] / n_1015["prev_close"] - 1) * 100

rows = []
for sym, g in t.groupby("symbol"):
    f = load(sym)
    if f is None:
        continue
    daily = f.groupby("date").agg(h=("high", "max"), l=("low", "min"), c=("close", "last"))
    tr = pd.concat([daily["h"] - daily["l"], (daily["h"] - daily["c"].shift()).abs(),
                    (daily["l"] - daily["c"].shift()).abs()], axis=1).max(axis=1)
    atr_pct = (tr.ewm(alpha=1 / 14, adjust=False).mean() / daily["c"] * 100).shift(1)
    cv = f[f["mins"] == 610].set_index("date")["cumvol"]
    rvol = cv / cv.shift(1).rolling(10, min_periods=5).mean()
    for r in g.itertuples():
        d = f[f["date"] == r.date]
        if d.empty:
            continue
        side = 1 if r.side == "LONG" else -1
        mins = d["mins"].values
        o, h, l, c, vw = (d[x].values for x in ("open", "high", "low", "close", "vwap"))
        pc = d["prev_close"].iat[0]
        i0 = int(np.searchsorted(mins, r.entry_min))
        iex = int(np.searchsorted(mins, 900))
        if i0 >= len(o) or iex >= len(o):
            continue
        e = r.entry
        snap = d[d["mins"] == 610]
        mv = lambda px: (px - e) / e * 100 * side  # noqa: E731
        seg_h, seg_l = h[i0:iex], l[i0:iex]
        mfe = mv(seg_h.max()) if side == 1 else mv(seg_l.min())
        mae = mv(seg_l.min()) if side == 1 else mv(seg_h.max())
        k_mfe = (np.argmax(seg_h) if side == 1 else np.argmin(seg_l)) + i0
        row = {"date": r.date, "symbol": sym, "sector": r.sector, "side": r.side, "period": r.period,
               "net": r.net, "qty": r.qty, "entry": e, "exit": r.exit,
               "stock_move_1015": ((snap["close"].iat[0] / pc - 1) * 100 * side) if len(snap) else np.nan,
               "rvol": rvol.get(r.date, np.nan), "nifty_move_1015": n_chg.get(r.date, np.nan) * side,
               "nifty_vs_ema20": n_dist.get(r.date, np.nan) * side, "gap": (o[0] / pc - 1) * 100 * side,
               "entry_delay_min": r.entry_min - 615, "vwap_dist": (e - vw[i0]) / e * 100 * side,
               "atr_pct": atr_pct.get(r.date, np.nan), "mfe": mfe, "mae": mae,
               "mfe_time": int(mins[k_mfe]), "ret_eod": mv(o[iex])}
        for hh in (11, 12, 13, 14):
            k = int(np.searchsorted(mins, hh * 60))
            row[f"ret_{hh}"] = mv(o[k]) if k < len(o) and k > i0 else np.nan

        def pnl(px):
            px = px * (1 - side * SLIP)
            gross = (px - e) * r.qty * side
            bv, sv = (e * r.qty, px * r.qty) if side == 1 else (px * r.qty, e * r.qty)
            return gross - charges(bv, sv)

        for hh in (12, 13, 14):
            k = int(np.searchsorted(mins, hh * 60))
            row[f"exit_{hh}"] = pnl(o[k]) if i0 < k < len(o) else r.net
        # trailing stops: activate after +act %, trail by tr %
        for act, trl in ((0.5, 0.5), (1.0, 0.75), (1.5, 1.0)):
            best, px_out = e, None
            for j in range(i0, iex):
                fav = h[j] if side == 1 else l[j]
                stop_px = best * (1 - side * trl / 100) if mv(best) >= act else None
                if stop_px is not None and ((side == 1 and l[j] <= stop_px) or (side == -1 and h[j] >= stop_px)):
                    px_out = min(o[j], stop_px) if side == 1 else max(o[j], stop_px)
                    break
                if (side == 1 and fav > best) or (side == -1 and fav < best):
                    best = fav
            row[f"trail_{act}_{trl}"] = pnl(px_out if px_out is not None else o[iex])
        rows.append(row)

a = pd.DataFrame(rows)
a.to_csv(out / "trade_features.csv", index=False)
L = ["# Loss analysis - Sector Pullback final (non-F&O)", "", f"Trades: {len(a)}", ""]


def both(df, col, bins, labels):
    df = df.assign(bucket=pd.cut(df[col], bins, labels=labels))
    p = df.pivot_table(index="bucket", columns="period", values="net", aggfunc=["count", "sum"], observed=False)
    p.columns = [f"{x}_{y}" for x, y in p.columns]
    return p.round(0)


L += ["## Winners vs losers", "",
      a.assign(result=np.where(a["net"] > 0, "win", "loss")).groupby(["period", "result"]).agg(
          n=("net", "size"), net=("net", "sum"), avg=("net", "mean"), mfe=("mfe", "median"),
          mae=("mae", "median"), mfe_time=("mfe_time", "median")).round(2).to_markdown(), ""]
lost = a[a["net"] <= 0]
L += ["## Losing trades: did they ever go our way?", "",
      f"- never went +0.5% in our favour: {(lost['mfe'] < 0.5).mean():.0%}",
      f"- went +1% or more first, then ended in loss (gave back): {(lost['mfe'] >= 1).mean():.0%} "
      f"({int((lost['mfe'] >= 1).sum())} trades)",
      f"- median worst move against: {lost['mae'].median():.2f} %", ""]
feats = {
    "stock_move_1015": ([-9, 1.5, 2.5, 99], ["<1.5%", "1.5-2.5%", ">2.5%"]),
    "rvol": ([0, 2, 3, 999], ["1.5-2x", "2-3x", ">3x"]),
    "nifty_move_1015": ([-9, 0.2, 0.5, 99], ["0-0.2%", "0.2-0.5%", ">0.5%"]),
    "nifty_vs_ema20": ([-99, 1, 2.5, 99], ["<1%", "1-2.5%", ">2.5%"]),
    "gap": ([-99, 0.5, 1.5, 99], ["<0.5%", "0.5-1.5%", ">1.5%"]),
    "entry_delay_min": ([-1, 30, 90, 999], ["<=30 min", "30-90 min", ">90 min"]),
    "vwap_dist": ([-99, 0.5, 1.0, 99], ["<0.5%", "0.5-1%", ">1%"]),
    "atr_pct": ([0, 2.5, 3.5, 99], ["<2.5%", "2.5-3.5%", ">3.5%"]),
}
for col, (b, lb) in feats.items():
    L += [f"## By {col}", "", both(a, col, b, lb).to_markdown(), ""]
L += ["## By side", "", a.pivot_table(index="side", columns="period", values="net",
                                       aggfunc=["count", "sum"]).round(0).to_markdown(), ""]
L += ["## By weekday", "", a.assign(wd=pd.to_datetime(a["date"]).dt.day_name()).pivot_table(
    index="wd", columns="period", values="net", aggfunc="sum").round(0).to_markdown(), ""]
ex = ["net", "exit_12", "exit_13", "exit_14", "trail_0.5_0.5", "trail_1.0_0.75", "trail_1.5_1.0"]
L += ["## What-if exits (same entries, Rs after charges)", "",
      a.groupby("period")[ex].sum().round(0).rename(columns={
          "net": "15:00 (now)", "exit_12": "12:00", "exit_13": "13:00", "exit_14": "14:00",
          "trail_0.5_0.5": "trail +0.5/0.5%", "trail_1.0_0.75": "trail +1/0.75%",
          "trail_1.5_1.0": "trail +1.5/1%"}).to_markdown(), "",
      "## Average path (% move in our favour)", "",
      a.groupby("period")[["ret_11", "ret_12", "ret_13", "ret_14", "ret_eod", "mfe", "mae"]].mean().round(2)
      .to_markdown(), ""]
L += ["## By sector (all periods)", "",
      a.pivot_table(index="sector", columns="period", values="net", aggfunc=["count", "sum"]).round(0)
      .sort_values(("sum", "OOS")).to_markdown(), ""]
L += ["## Worst 10 trades", "", a.nsmallest(10, "net")[
    ["date", "symbol", "side", "period", "net", "stock_move_1015", "rvol", "gap", "mfe", "mae"]].round(2)
    .to_markdown(index=False)]
(out / "summary.md").write_text("\n".join(L))
print("\n".join(L))
