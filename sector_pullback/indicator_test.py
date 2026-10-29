#!/usr/bin/env python3
"""Indicator / S-R / EMA / volume / POC / Nifty-trend filters on top of the Sector Pullback trades.

Two trade pools (Rs 50,000 per trade, Rs 20/order, 289 non-F&O, skip Monday):
  FINAL  = one scan 10:15
  SCAN5  = every 5 min 10:15-14:30, only on days whose direction was clear at 10:15
For every trade we compute indicators using ONLY candles closed before entry, then check each
filter: keep the trade if the condition is true. A filter is useful only if it improves BOTH years.
(Screening is done on the existing trades; the winners are then re-run properly at the end.)
"""
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
tl = [f"{x // 60:02d}:{x % 60:02d}" for x in range(615, 871, 5)]
scan5 = replace(final, decision_time=tl[0], extra_scans=tuple(tl[1:]), last_entry="14:30", confirm_time="10:15")
pools = {"FINAL": run(m, final, days), "SCAN5": run(m, scan5, days)}

# ---------- per-stock helper series (all causal)
ema50, rsi, dly = {}, {}, {}
for s, f in m.f.items():
    c = f["close"]
    ema50[s] = c.ewm(span=50, adjust=False, min_periods=50).mean().values
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi[s] = (100 - 100 / (1 + up / dn.replace(0, np.nan))).values
    g = f.groupby("date")
    dd = pd.DataFrame({"h": g["high"].max(), "l": g["low"].min(), "c": g["close"].last()})
    dd["e20"] = dd["c"].ewm(span=20, adjust=False, min_periods=20).mean()
    dd["e50"] = dd["c"].ewm(span=50, adjust=False, min_periods=50).mean()
    dly[s] = dd
nf = m.nifty
n_ema = nf["ema"].values
n_idx = {(d, mi): i for i, (d, mi) in enumerate(zip(nf["date"].values, nf["mins"].values))}
nd = m.nifty_daily_close
n50 = nd.ewm(span=50, adjust=False, min_periods=50).mean()


def poc(prices, vols, lo, hi, bins=40):
    if hi <= lo or vols.sum() <= 0:
        return np.nan
    edges = np.linspace(lo, hi, bins + 1)
    k = np.clip(np.digitize(prices, edges) - 1, 0, bins - 1)
    w = np.bincount(k, weights=vols, minlength=bins)
    j = int(np.argmax(w))
    return (edges[j] + edges[j + 1]) / 2


def features(t: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for r in t.itertuples():
        s, day = r.symbol, r.date
        sd = 1 if r.side == "LONG" else -1
        f = m.f[s]
        a, b = m.slices[s][day]
        mins = f["mins"].values[a:b]
        i0 = int(np.searchsorted(mins, r.entry_min))      # entry bar
        k = a + i0 - 1                                    # last closed bar before entry
        c = f["close"].values
        o, h, l, v = f["open"].values, f["high"].values, f["low"].values, f["volume"].values
        vw = f["vwap"].values
        px = c[k]
        dd = dly[s]
        di = dd.index.get_loc(day)
        prev = dd.iloc[di - 1] if di > 0 else None
        pdh, pdl = (prev["h"], prev["l"]) if prev is not None else (np.nan, np.nan)
        # previous day POC
        days_sorted = list(m.slices[s].keys())
        pi = days_sorted.index(day)
        poc_prev = np.nan
        if pi > 0:
            pa, pb = m.slices[s][days_sorted[pi - 1]]
            tp = (h[pa:pb] + l[pa:pb] + c[pa:pb]) / 3
            poc_prev = poc(tp, v[pa:pb], l[pa:pb].min(), h[pa:pb].max())
        tp = (h[a:k + 1] + l[a:k + 1] + c[a:k + 1]) / 3
        poc_today = poc(tp, v[a:k + 1], l[a:k + 1].min(), h[a:k + 1].max())
        orb = mins < 585
        orh, orl = h[a:b][orb].max(), l[a:b][orb].min()
        day_hi, day_lo = h[a:k + 1].max(), l[a:k + 1].min()
        vavg = v[max(0, k - 20):k].mean()
        ni = n_idx.get((day, mins[i0 - 1]))
        nclose = nf["close"].values[ni] if ni is not None else np.nan
        nema = n_ema[ni] if ni is not None else np.nan
        nchg = (nclose / nf["prev_close"].values[ni] - 1) * 100 * sd if ni is not None else np.nan
        dir_ = lambda x: x * sd  # noqa: E731
        room = ((pdh - px) / px * 100) if sd == 1 else ((px - pdl) / px * 100)
        rows.append({
            "EMA: stock above 5-min EMA50": dir_(px - ema50[s][k]) > 0,
            "EMA: stock daily trend (prev close vs 20-day EMA)": prev is not None and dir_(prev["c"] - prev["e20"]) > 0,
            "EMA: stock daily trend (prev close vs 50-day EMA)": prev is not None and dir_(prev["c"] - prev["e50"]) > 0,
            "S/R: already broke prev-day high (long) / low (short)": dir_(px - (pdh if sd == 1 else pdl)) > 0,
            "S/R: >=1% room to prev-day high/low, or already broken": (room >= 1) or (room <= 0),
            "S/R: broke opening-range (30 min) high/low": dir_(px - (orh if sd == 1 else orl)) > 0,
            "S/R: within 0.5% of day high (long) / low (short)": (abs(px - (day_hi if sd == 1 else day_lo)) / px * 100) <= 0.5,
            "VOL: signal candle volume >= 1.5x last 20 candles": v[k] >= 1.5 * vavg if vavg > 0 else False,
            "VOL: signal candle volume >= 1.0x last 20 candles": v[k] >= 1.0 * vavg if vavg > 0 else False,
            "POC: price above prev-day POC (long) / below (short)": dir_(px - poc_prev) > 0 if not np.isnan(poc_prev) else False,
            "POC: price above today's POC (long) / below (short)": dir_(px - poc_today) > 0 if not np.isnan(poc_today) else False,
            "RSI: 5-min RSI 55-70 (long) / 30-45 (short)": (55 <= rsi[s][k] <= 70) if sd == 1 else (30 <= rsi[s][k] <= 45),
            "RSI: 5-min RSI not extreme (<75 long, >25 short)": (rsi[s][k] < 75) if sd == 1 else (rsi[s][k] > 25),
            "VWAP: entry within 1% of VWAP": abs(px - vw[k]) / px * 100 <= 1.0,
            "NIFTY: 5-min close on our side of its EMA20": dir_(nclose - nema) > 0 if ni is not None else False,
            "NIFTY: moved >= 0.3% our way at entry": nchg >= 0.3 if ni is not None else False,
            "NIFTY: prev close on our side of 50-day EMA": dir_(nd.shift(1).get(day, np.nan) - n50.shift(1).get(day, np.nan)) > 0,
        })
    return pd.concat([t.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


report = ["# Indicator / S-R / EMA / volume / POC / Nifty filters", "",
          f"Window {days[0]} -> {days[-1]}, Rs 50,000 per trade, Rs 20/order. Y1 = first year, Y2 = second.",
          "A filter keeps only trades where the condition was TRUE at entry. 'both' = better avg per trade in "
          "both years AND net profit in both years.", ""]
summary_rows = []
for pname, t in pools.items():
    t = features(t)
    t["Y"] = np.where(pd.to_datetime(t["date"]) < start + pd.DateOffset(years=1), "Y1", "Y2")
    t.to_csv(out / f"features_{pname}.csv", index=False)
    base = t.groupby("Y")["net"].agg(["count", "sum", "mean"])
    rows = [{"filter": "(no filter)", "trades": len(t), "Y1_net": round(base.loc["Y1", "sum"]),
             "Y2_net": round(base.loc["Y2", "sum"]), "Y1_avg": round(base.loc["Y1", "mean"]),
             "Y2_avg": round(base.loc["Y2", "mean"]), "kept_%": 100, "both": ""}]
    fcols = [c for c in t.columns if ":" in c]
    for fc in fcols:
        k = t[t[fc].astype(bool)]
        g = k.groupby("Y")["net"].agg(["count", "sum", "mean"]).reindex(["Y1", "Y2"]).fillna(0)
        good = all(g.loc[y, "mean"] > base.loc[y, "mean"] and g.loc[y, "sum"] > 0 for y in ("Y1", "Y2"))
        rows.append({"filter": fc, "trades": len(k), "Y1_net": round(g.loc["Y1", "sum"]),
                     "Y2_net": round(g.loc["Y2", "sum"]), "Y1_avg": round(g.loc["Y1", "mean"]),
                     "Y2_avg": round(g.loc["Y2", "mean"]), "kept_%": round(len(k) / len(t) * 100),
                     "both": "YES" if good else ""})
    res = pd.DataFrame(rows)
    res.to_csv(out / f"filters_{pname}.csv", index=False)
    report += [f"## Pool {pname} ({len(t)} trades)", "", res.to_markdown(index=False), ""]
    for r in rows[1:]:
        summary_rows.append({"pool": pname, **r})
    # best pairs among filters that passed alone
    passed = [r["filter"] for r in rows[1:] if r["both"] == "YES"]
    pairs = []
    for i in range(len(passed)):
        for j in range(i + 1, len(passed)):
            k = t[t[passed[i]].astype(bool) & t[passed[j]].astype(bool)]
            g = k.groupby("Y")["net"].agg(["count", "sum"]).reindex(["Y1", "Y2"]).fillna(0)
            pairs.append({"filter A": passed[i], "filter B": passed[j], "trades": len(k),
                          "Y1_net": round(g.loc["Y1", "sum"]), "Y2_net": round(g.loc["Y2", "sum"])})
    if pairs:
        report += [f"### {pname}: pairs of filters that passed alone", "",
                   pd.DataFrame(pairs).sort_values("trades", ascending=False).to_markdown(index=False), ""]
(out / "summary.md").write_text("\n".join(report))
print("\n".join(report))
