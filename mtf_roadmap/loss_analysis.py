#!/usr/bin/env python3
"""Loss analysis for a trades CSV (from intraday.py / backtest.py) using the 5-min candles.

Per trade, from entry candle to 15:10:
  MFE_R  = best move in our favour (in R) before the real exit
  MAE_R  = worst move against us (in R) over the whole rest of the day
  hunted = SL was hit, but later the same day price reached the original target
  eod_R  = result if we had just held from entry to 15:10 (no SL, no target)
What-if (same entries, same day): fixed SL of 0.5 / 0.75 / 1.0 / 1.5 % with 2R target, exit 15:10.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import charges, load_csv

EXIT_MIN = 15 * 60 + 10


def sim(o, h, l, mins, i0, side, entry, stop, target):
    for j in range(i0, len(o)):
        if mins[j] >= EXIT_MIN:
            return o[j], "TIME"
        if side == 1:
            if o[j] <= stop:
                return o[j], "SL"
            if l[j] <= stop:
                return stop, "SL"
            if h[j] >= target:
                return max(o[j], target), "TARGET"
        else:
            if o[j] >= stop:
                return o[j], "SL"
            if h[j] >= stop:
                return stop, "SL"
            if l[j] <= target:
                return min(o[j], target), "TARGET"
    return None, "TIME"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", required=True)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    t = pd.read_csv(args.trades)
    t["date"] = pd.to_datetime(t["date"]).dt.normalize()
    rows = []
    for sym, g in t.groupby("symbol"):
        pth = next((Path(args.data_dir) / f"{sym}{e}" for e in (".csv.gz", ".csv")
                    if (Path(args.data_dir) / f"{sym}{e}").exists()), None)
        if pth is None:
            continue
        df = load_csv(pth)
        df["date"] = df.index.normalize()
        df["mins"] = df.index.hour * 60 + df.index.minute
        by_day = {d: x for d, x in df.groupby("date")}
        for r in g.itertuples():
            day = by_day.get(r.date)
            if day is None:
                continue
            side = 1 if r.side == "LONG" else -1
            mins = day["mins"].values
            o, h, l, c = (day[x].values for x in ("open", "high", "low", "close"))
            i0 = int(np.searchsorted(mins, r.entry_min))
            iend = int(np.searchsorted(mins, EXIT_MIN))
            ix = int(np.searchsorted(mins, r.exit_min))
            risk = abs(r.entry - r.stop)
            if risk <= 0 or i0 >= len(o):
                continue
            fav = (h[i0:ix + 1].max() - r.entry) if side == 1 else (r.entry - l[i0:ix + 1].min())
            adv = (r.entry - l[i0:iend].min()) if side == 1 else (h[i0:iend].max() - r.entry)
            eod_px = o[iend] if iend < len(o) else c[-1]
            hunted = False
            if r.reason == "SL" and ix + 1 < iend:
                later = h[ix + 1:iend].max() >= r.target if side == 1 else l[ix + 1:iend].min() <= r.target
                hunted = bool(later)
            row = {"symbol": sym, "date": r.date.date(), "period": r.period, "side": r.side, "reason": r.reason,
                   "net": r.net, "R": r.R, "risk_pct": risk / r.entry * 100,
                   "MFE_R": fav / risk, "MAE_R": adv / risk, "hunted": hunted,
                   "eod_R": (eod_px - r.entry) * side / risk}
            for slp in (0.5, 0.75, 1.0, 1.5):
                st = r.entry * (1 - side * slp / 100)
                tg = r.entry * (1 + side * 2 * slp / 100)
                px, _ = sim(o, h, l, mins, i0, side, r.entry, st, tg)
                if px is None:
                    px = c[-1]
                rk = abs(r.entry - st)
                qty = int(min(1000 / rk, 200_000 / r.entry))
                gross = (px - r.entry) * qty * side
                bv, sv = (r.entry * qty, px * qty) if side == 1 else (px * qty, r.entry * qty)
                row[f"sl{slp}_net"] = gross - charges(bv, sv)
            rows.append(row)
    a = pd.DataFrame(rows)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    a.to_csv(out / "trade_analysis.csv", index=False)

    L = ["# Loss analysis", "", f"Trades analysed: {len(a)} (source: {args.trades})", ""]
    for p, g in a.groupby("period"):
        sl = g[g["reason"] == "SL"]
        L += [f"## {p}", "",
              f"- Trades {len(g)}, net Rs {g['net'].sum():,.0f}",
              f"- SL trades {len(sl)}: never moved +0.5R in our favour = {(sl['MFE_R'] < 0.5).mean():.0%}, "
              f"never moved +1R = {(sl['MFE_R'] < 1).mean():.0%}",
              f"- SL trades where price LATER reached our target the same day (stop hunted): "
              f"{sl['hunted'].mean():.0%} ({int(sl['hunted'].sum())})",
              f"- Hold till 15:10 with no SL/target: avg {g['eod_R'].mean():+.2f} R per trade, "
              f"positive {(g['eod_R'] > 0).mean():.0%}",
              f"- Median MAE (worst move against, whole day): {g['MAE_R'].median():.2f} R", "",
              "What-if: same entries, fixed SL %, target 2x SL, after charges (Rs):", ""]
        wi = pd.DataFrame({f"SL {s}%": [g[f'sl{s}_net'].sum().round(0), (g[f'sl{s}_net'] > 0).mean().round(3)]
                           for s in (0.5, 0.75, 1.0, 1.5)}, index=["net", "win_rate"])
        L += [wi.to_markdown(), "",
              "MFE of SL trades (how far they went our way first):", "",
              sl.groupby(pd.cut(sl["MFE_R"], [-1, 0.25, 0.5, 1, 1.5, 99]), observed=True).size()
              .rename("trades").to_markdown(), ""]
        by = g.groupby("side")[["net", "eod_R"]].agg({"net": "sum", "eod_R": "mean"}).round(2)
        L += ["By side:", "", by.to_markdown(), ""]
    (out / "summary.md").write_text("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
