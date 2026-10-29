#!/usr/bin/env python3
"""
Intraday Roadmap: 1D trend -> intraday key levels -> 5m liquidity sweep + MSS/CISD.

The swing roadmap (1D -> 4H -> 15M) rarely reached its target inside one day, so this
version keeps the 1D trend filter but uses levels that are close enough to trade intraday.

LONG (SHORT = mirror image):
1D   uptrend = close > EMA50, EMA20 > EMA50, EMA50 rising (completed days only)
Lvl  key supports for today: previous day low (PDL), previous close (PDC), opening-range low
     (09:15-09:45), and previous day high (PDH) if today opened above it.
     Only levels below the 09:40 candle close are used.
5m   1. sweep : a 5m candle's low goes to/below a level (+0.1 ATR buffer) after 09:45
     2. a 5m CLOSE more than 0.3 ATR below the level = level broken -> that level is dead
     3. confirm within 12 candles (1 hour) of the sweep:
          MSS  = 5m close above the high of the previous 3 candles
          CISD = 5m close above the open of the first red candle of the leg into the sweep
     4. entry at the next 5m open; scanning starts at scan_start (09:45 / 10:30 / 11:30), last 13:30
Risk SL = sweep low - 0.05 %, risk 0.3 % .. 1.5 % of price
     target = 1.5R / 2R, or "level" = next intraday resistance (day high so far, ORH, PDH)
     if it gives >= 1.5R (capped 3R). Optional SL to entry after +1R. Exit 15:10.
     Rs 1,000 risk/trade, max Rs 2 L/position, 1 trade/stock/day, max 3/day, stop after 2 losses.
Costs: Angel One intraday charges + 0.03 % slippage per fill.
Parameters chosen on first 2/3 of days (in-sample), reported on last 1/3 (out-of-sample).
"""
from __future__ import annotations

import argparse
import itertools
import json
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import charges, load_csv, stats


@dataclass(frozen=True)
class IP:
    trigger: str = "MSS"        # MSS | CISD
    target: str = "2R"          # 1.5R | 2R | level
    breakeven: bool = False
    use_trend: bool = True
    sides: str = "both"
    sweep_buf_atr: float = 0.10
    break_atr: float = 0.30
    confirm_bars: int = 12
    sl_min_pct: float = 0.3
    sl_max_pct: float = 1.5
    risk_rs: float = 1000.0
    max_notional: float = 200_000.0
    max_trades_day: int = 3
    max_losses_day: int = 2
    slippage_pct: float = 0.03
    or_end: int = 9 * 60 + 45
    scan_start: int = 9 * 60 + 45   # sweeps/entries only from this time (minutes)
    entry_end: int = 13 * 60 + 30
    exit_min: int = 15 * 60 + 10


class IStock:
    def __init__(self, sym: str, df5: pd.DataFrame):
        self.sym = sym
        df5 = df5.copy()
        df5["date"] = df5.index.normalize()
        df5["mins"] = df5.index.hour * 60 + df5.index.minute
        self.o, self.h, self.l, self.c = (df5[x].values for x in ("open", "high", "low", "close"))
        self.mins = df5["mins"].values
        g = df5.groupby("date")
        d = pd.DataFrame({"high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last(),
                          "n": g.size()})
        d = d[d["n"] >= 60]
        d["ema20"] = d["close"].ewm(span=20, adjust=False, min_periods=20).mean()
        d["ema50"] = d["close"].ewm(span=50, adjust=False, min_periods=50).mean()
        tr = pd.concat([d["high"] - d["low"], (d["high"] - d["close"].shift()).abs(),
                        (d["low"] - d["close"].shift()).abs()], axis=1).max(axis=1)
        d["atr"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
        up = (d["close"] > d["ema50"]) & (d["ema20"] > d["ema50"]) & (d["ema50"] > d["ema50"].shift(5))
        dn = (d["close"] < d["ema50"]) & (d["ema20"] < d["ema50"]) & (d["ema50"] < d["ema50"].shift(5))
        d["trend"] = np.where(up, 1, np.where(dn, -1, 0))
        pos = np.flatnonzero(df5["date"].values[1:] != df5["date"].values[:-1]) + 1
        starts, ends = np.r_[0, pos], np.r_[pos, len(df5)]
        sl = {df5["date"].iat[a]: (a, b) for a, b in zip(starts, ends)}
        self.plans = {}
        for i in range(1, len(d)):
            prev, day = d.iloc[i - 1], d.index[i]
            if pd.isna(prev["atr"]) or pd.isna(prev["ema50"]) or day not in sl:
                continue
            self.plans[day] = {"trend": int(prev["trend"]), "atr": float(prev["atr"]), "pdh": float(prev["high"]),
                               "pdl": float(prev["low"]), "pdc": float(prev["close"]), "slice": sl[day]}

    def trade(self, day, side: int, p: IP):
        pl = self.plans.get(day)
        if pl is None:
            return None
        a, b = pl["slice"]
        o, h, l, c, mins = self.o[a:b], self.h[a:b], self.l[a:b], self.c[a:b], self.mins[a:b]
        n = len(o)
        orb = np.flatnonzero(mins < p.or_end)
        if len(orb) < 3 or orb[-1] + 2 >= n:
            return None
        k0 = orb[-1] + 1                       # first candle after the opening range
        ks = int(np.searchsorted(mins, max(p.or_end, p.scan_start)))
        if ks >= n - 1:
            return None
        orh, orl, ref = h[orb].max(), l[orb].min(), c[orb[-1]]
        atr, day_open = pl["atr"], o[0]
        if side == 1:
            lv = [pl["pdl"], pl["pdc"], orl] + ([pl["pdh"]] if day_open > pl["pdh"] else [])
            levels = sorted({x for x in lv if x < ref}, reverse=True)
        else:
            lv = [pl["pdh"], pl["pdc"], orh] + ([pl["pdl"]] if day_open < pl["pdl"] else [])
            levels = sorted({x for x in lv if x > ref})
        if not levels:
            return None
        buf, brk = p.sweep_buf_atr * atr, p.break_atr * atr
        alive = {x: True for x in levels}
        sweep_i, ext, entry_i = None, None, None
        for j in range(ks, n - 1):
            if mins[j] > p.entry_end:
                break
            for x in levels:                   # broken levels
                if alive[x] and ((side == 1 and c[j] < x - brk) or (side == -1 and c[j] > x + brk)):
                    alive[x] = False
            if sweep_i is not None and j - sweep_i > p.confirm_bars:
                sweep_i = None
            hit = [x for x in levels if alive[x] and
                   ((side == 1 and l[j] <= x + buf) or (side == -1 and h[j] >= x - buf))]
            if hit and sweep_i is None:
                sweep_i, ext = j, (l[j] if side == 1 else h[j])
            elif sweep_i is not None:
                ext = min(ext, l[j]) if side == 1 else max(ext, h[j])
            if sweep_i is None or not any(alive.values()):
                continue
            if p.trigger == "MSS":
                if j - 3 < k0 - 6:
                    continue
                ok = c[j] > h[j - 3:j].max() if side == 1 else c[j] < l[j - 3:j].min()
            else:
                e = int(np.argmin(l[sweep_i:j + 1]) + sweep_i) if side == 1 else int(np.argmax(h[sweep_i:j + 1]) + sweep_i)
                k = e
                if side == 1:
                    while k > 0 and c[k] >= o[k]:
                        k -= 1
                    while k > 0 and c[k - 1] < o[k - 1]:
                        k -= 1
                    ok = j > e and c[j] > o[k]
                else:
                    while k > 0 and c[k] <= o[k]:
                        k -= 1
                    while k > 0 and c[k - 1] > o[k - 1]:
                        k -= 1
                    ok = j > e and c[j] < o[k]
            if ok:
                entry_i = j + 1
                break
        if entry_i is None:
            return None

        slip = p.slippage_pct / 100
        raw = o[entry_i]
        stop = ext * (1 - 0.0005) if side == 1 else ext * (1 + 0.0005)
        rp = (raw - stop) / raw * 100 * side
        if rp <= 0 or rp > p.sl_max_pct:
            return None
        if rp < p.sl_min_pct:
            stop = raw * (1 - side * p.sl_min_pct / 100)
        entry = raw * (1 + side * slip)
        risk = abs(entry - stop)
        if p.target in ("1.5R", "2R"):
            target = entry + side * float(p.target[:-1]) * risk
        else:
            if side == 1:
                cands = [x for x in (h[:entry_i].max(), orh, pl["pdh"]) if x > entry]
                lvl = min(cands) if cands else None
            else:
                cands = [x for x in (l[:entry_i].min(), orl, pl["pdl"]) if x < entry]
                lvl = max(cands) if cands else None
            if lvl is None or abs(lvl - entry) / risk < 1.5:
                return None
            target = entry + side * min(abs(lvl - entry) / risk, 3.0) * risk
        qty = int(min(p.risk_rs / risk, p.max_notional / entry))
        if qty < 1:
            return None
        sl_, be, reason, px, j = stop, False, None, None, entry_i
        for j in range(entry_i, n):
            if mins[j] >= p.exit_min:
                px, reason = o[j], "TIME"
                break
            if side == 1:
                if o[j] <= sl_:
                    px, reason = o[j], "SL"
                elif l[j] <= sl_:
                    px, reason = sl_, "SL"
                elif h[j] >= target:
                    px, reason = max(o[j], target), "TARGET"
            else:
                if o[j] >= sl_:
                    px, reason = o[j], "SL"
                elif h[j] >= sl_:
                    px, reason = sl_, "SL"
                elif l[j] <= target:
                    px, reason = min(o[j], target), "TARGET"
            if reason:
                break
            if p.breakeven and not be and side * (c[j] - entry) >= risk:
                sl_, be = entry, True
        if reason is None:
            px, reason = c[-1], "TIME"
        if reason == "SL" and be:
            reason = "BREAKEVEN"
        px = px * (1 - side * slip)
        gross = (px - entry) * qty * side
        bv, sv = (entry * qty, px * qty) if side == 1 else (px * qty, entry * qty)
        ch = charges(bv, sv)
        hm = lambda m: f"{m // 60:02d}:{m % 60:02d}"  # noqa: E731
        return {"date": pd.Timestamp(day).date(), "symbol": self.sym, "side": "LONG" if side == 1 else "SHORT",
                "entry_time": hm(int(mins[entry_i])), "exit_time": hm(int(mins[j])),
                "entry_min": int(mins[entry_i]), "exit_min": int(mins[j]), "qty": qty,
                "entry": round(entry, 2), "stop": round(stop, 2), "target": round(target, 2),
                "exit": round(px, 2), "reason": reason, "gross": round(gross, 2),
                "charges": round(ch, 2), "net": round(gross - ch, 2), "R": round((gross - ch) / p.risk_rs, 2)}


def run(stocks: list[IStock], p: IP, days) -> pd.DataFrame:
    out = []
    for day in days:
        trades = []
        for s in stocks:
            pl = s.plans.get(day)
            if pl is None:
                continue
            sides = ([pl["trend"]] if pl["trend"] else []) if p.use_trend else [1, -1]
            if p.sides == "long":
                sides = [x for x in sides if x == 1]
            elif p.sides == "short":
                sides = [x for x in sides if x == -1]
            best = None
            for sd in sides:
                t = s.trade(day, sd, p)
                if t and (best is None or t["entry_min"] < best["entry_min"]):
                    best = t
            if best:
                trades.append(best)
        trades.sort(key=lambda t: (t["entry_min"], t["symbol"]))
        taken = []
        for t in trades:
            losses = sum(1 for x in taken if x["net"] < 0 and x["exit_min"] <= t["entry_min"])
            if len(taken) >= p.max_trades_day or losses >= p.max_losses_day:
                break
            taken.append(t)
        out += taken
    return pd.DataFrame(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--symbols-file", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--is-frac", type=float, default=2 / 3)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    wanted = {x.strip().upper() for x in Path(args.symbols_file).read_text().splitlines() if x.strip()}
    stocks = []
    for pth in sorted(Path(args.data_dir).iterdir()):
        name = pth.name
        for s in (".csv.gz", ".csv"):
            name = name.removesuffix(s)
        if name in wanted:
            try:
                stocks.append(IStock(name, load_csv(pth)))
            except Exception as exc:  # noqa: BLE001
                print("skip", name, exc)
    print(f"Loaded {len(stocks)}/{len(wanted)} stocks")
    days = sorted(set().union(*(set(s.plans) for s in stocks)))
    cut = int(len(days) * args.is_frac)
    is_days, oos_days = days[:cut], days[cut:]
    f = lambda d: pd.Timestamp(d).date()  # noqa: E731
    print(f"IS {f(is_days[0])} -> {f(is_days[-1])} ({len(is_days)}) | OOS {f(oos_days[0])} -> {f(oos_days[-1])} ({len(oos_days)})")

    rows = []
    hm = lambda m: f"{m // 60:02d}:{m % 60:02d}"  # noqa: E731
    for trig, tgt, st in itertools.product(["MSS", "CISD"], ["1.5R", "2R", "level"], [585, 630, 690]):
        be = False
        p = IP(trigger=trig, target=tgt, breakeven=be, scan_start=st)
        si, so = stats(run(stocks, p, is_days)), stats(run(stocks, p, oos_days))
        rows.append({"trigger": trig, "target": tgt, "start": hm(st), "scan_start": st, "breakeven": be,
                     **{f"IS_{k}": v for k, v in si.items()}, **{f"OOS_{k}": v for k, v in so.items()}})
        print(f"{trig:4s} {tgt:5s} from {hm(st)} | IS {si['net']:>9,} pf {si['pf']:.2f} n={si['trades']:>4} wr {si['win_rate']}"
              f" | OOS {so['net']:>9,} pf {so['pf']:.2f} n={so['trades']:>4} wr {so['win_rate']}", flush=True)
    g = pd.DataFrame(rows)
    g.to_csv(out / "grid.csv", index=False)
    elig = g[g["IS_trades"] >= 60]
    br = (elig if len(elig) else g).sort_values(["IS_pf", "IS_net"], ascending=False).iloc[0]
    best = IP(trigger=br["trigger"], target=br["target"], breakeven=bool(br["breakeven"]),
              scan_start=int(br["scan_start"]))
    chosen = {"trigger": best.trigger, "target": best.target, "scan_from": hm(best.scan_start)}
    print("Chosen on in-sample:", chosen)

    variants = {"FULL (1D trend + level sweep + 5m confirm)": best,
                "without 1D trend": replace(best, use_trend=False),
                "LONG only": replace(best, sides="long"),
                "SHORT only": replace(best, sides="short")}
    abl = []
    for name, p in variants.items():
        r = {"variant": name}
        for lab, dd in (("IS", is_days), ("OOS", oos_days)):
            r.update({f"{lab}_{k}": v for k, v in stats(run(stocks, p, dd)).items()})
        abl.append(r)
    abl = pd.DataFrame(abl)
    abl.to_csv(out / "ablation.csv", index=False)
    t = run(stocks, best, days)
    lines = ["# Intraday Roadmap (1D trend -> intraday levels -> 5m sweep + MSS/CISD)", "",
             f"Stocks: {len(stocks)} | IS {f(is_days[0])} -> {f(is_days[-1])} | OOS {f(oos_days[0])} -> {f(oos_days[-1])}",
             f"Chosen params (in-sample only): {json.dumps(chosen)}", "",
             "## Variants (Rs, Rs 1,000 risk per trade, after charges)", "",
             abl[["variant", "IS_trades", "IS_net", "IS_win_rate", "IS_pf", "OOS_trades", "OOS_net",
                  "OOS_win_rate", "OOS_pf", "OOS_avg_R", "OOS_max_dd", "OOS_charges"]].to_markdown(index=False)]
    if not t.empty:
        oos_set = {f(d) for d in oos_days}
        t["period"] = np.where(t["date"].isin(oos_set), "OOS", "IS")
        t.to_csv(out / "trades_best.csv", index=False)
        gross = t.groupby("period")[["gross", "charges", "net"]].sum().round(0)
        lines += ["", "## Gross vs charges", "", gross.to_markdown(),
                  "", "## Exit reasons", "",
                  t.groupby("period")["reason"].value_counts().unstack(fill_value=0).to_markdown(),
                  "", "## By entry hour (OOS)", "",
                  t[t["period"] == "OOS"].groupby(t["entry_time"].str[:2])["net"].agg(["count", "sum"]).round(0).to_markdown()]
    lines += ["", "## Full grid", "", g.to_markdown(index=False)]
    (out / "summary.md").write_text("\n".join(lines))
    print("\n".join(lines[:14]))


if __name__ == "__main__":
    main()
