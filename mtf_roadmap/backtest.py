#!/usr/bin/env python3
"""
Multi-Timeframe Roadmap (1D -> 4H -> 15M) - backtest on saved 5-min data.

LONG rules (SHORT = mirror image). Everything for day D is decided from data that
was complete BEFORE the candle in question - no look-ahead.

1D  big picture (from completed days up to D-1)
    uptrend = close > EMA50, EMA20 > EMA50, EMA50 rising (vs 5 days ago)
4H  setup zone (levels confirmed before D starts)
    support = nearest swing low (4H pivots + daily pivots) below yesterday's close,
              not more than 1.5 x daily ATR away
    zone    = support - 0.25 ATR  ..  support + zone_w x ATR
15M exact entry (09:15 - 14:30)
    1. price comes down INTO the zone (15m low <= zone top)  -> pullback / liquidity sweep
       a 15m CLOSE below the zone bottom kills the setup for the day (support broken)
    2. confirmation (two variants from the IFVG/CISD/MSS idea):
         MSS  : 15m close above the high of the previous 3 candles (market structure shift)
         CISD : 15m close above the open of the down-move's first bearish candle
    3. entry at the next candle open
Risk
    SL     = lowest low since the zone was touched (sweep low) - 0.05 %
    target = "2R" fixed, or "level" = next swing high (resistance), used only if >= 1.5R (capped 4R)
    optional move SL to entry after +1R, exit at 15:00, SL/target checked on 5-min candles
    (same candle hits both -> SL). Rs 1,000 risk/trade, max Rs 2 L/position,
    1 trade per stock per day, max 3 a day, stop after 2 losses.
Costs: Angel One intraday charges + 0.03 % slippage per fill.

Parameters chosen on the first 2/3 of days (in-sample), reported on the last 1/3 (out-of-sample).
"""
from __future__ import annotations

import argparse
import itertools
import json
from dataclasses import dataclass, replace
from datetime import time as dtime
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class P:
    trigger: str = "MSS"          # MSS | CISD
    target: str = "2R"            # 2R | level
    breakeven: bool = True
    zone_w: float = 0.25          # zone height above support, in daily ATR
    use_trend: bool = True        # 1D trend filter
    sides: str = "both"           # both | long | short
    max_dist_atr: float = 1.5
    sl_min_pct: float = 0.3
    sl_max_pct: float = 2.0
    risk_rs: float = 1000.0
    max_notional: float = 200_000.0
    max_trades_day: int = 3
    max_losses_day: int = 2
    slippage_pct: float = 0.03
    entry_start: int = 9 * 60 + 45   # 15m candle must close at/after this (minutes)
    entry_end: int = 14 * 60 + 30
    exit_min: int = 15 * 60


def charges(buy_v: float, sell_v: float) -> float:
    def brok(v: float) -> float:
        return max(5.0, min(20.0, v * 0.10 / 100))
    b = brok(buy_v) + brok(sell_v)
    t = buy_v + sell_v
    exch, sebi = t * 0.0030699 / 100, t * 10 / 1e7
    return b + sell_v * 0.025 / 100 + exch + sebi + buy_v * 0.003 / 100 + (b + exch + sebi) * 0.18


def load_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    ts = next(c for c in df.columns if c.lower() in ("datetime", "timestamp", "date", "ts"))
    idx = pd.to_datetime(df[ts])
    if idx.dt.tz is not None:
        idx = idx.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    df = df.drop(columns=[ts])
    df.columns = [c.lower() for c in df.columns]
    df.index = pd.DatetimeIndex(idx)
    df = df[["open", "high", "low", "close", "volume"]].astype(float).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    t = df.index.time
    df = df[(t >= dtime(9, 15)) & (t < dtime(15, 30))]
    t = df.index.time
    return df[~((t >= dtime(15, 15)) & (df["volume"] == 0))]


def pivots(h: np.ndarray, l: np.ndarray, k: int = 2):
    n = len(h)
    ph, pl = np.zeros(n, bool), np.zeros(n, bool)
    for i in range(k, n - k):
        wh, wl = h[i - k:i + k + 1], l[i - k:i + k + 1]
        ph[i] = h[i] == wh.max() and (wh == h[i]).sum() == 1
        pl[i] = l[i] == wl.min() and (wl == l[i]).sum() == 1
    return ph, pl


class Stock:
    """Per-stock pre-computed day plans (bias, zone, levels) + 5m arrays per day."""

    def __init__(self, sym: str, df5: pd.DataFrame, k: int = 2):
        self.sym = sym
        df5 = df5.copy()
        df5["date"] = df5.index.normalize()
        df5["mins"] = df5.index.hour * 60 + df5.index.minute
        self.df5 = df5
        g = df5.groupby("date")
        d = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                          "close": g["close"].last(), "n": g.size()})
        d = d[d["n"] >= 60]
        d["ema20"] = d["close"].ewm(span=20, adjust=False, min_periods=20).mean()
        d["ema50"] = d["close"].ewm(span=50, adjust=False, min_periods=50).mean()
        tr = pd.concat([d["high"] - d["low"], (d["high"] - d["close"].shift()).abs(),
                        (d["low"] - d["close"].shift()).abs()], axis=1).max(axis=1)
        d["atr"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
        d["trend"] = 0
        up = (d["close"] > d["ema50"]) & (d["ema20"] > d["ema50"]) & (d["ema50"] > d["ema50"].shift(5))
        dn = (d["close"] < d["ema50"]) & (d["ema20"] < d["ema50"]) & (d["ema50"] < d["ema50"].shift(5))
        d.loc[up, "trend"], d.loc[dn, "trend"] = 1, -1
        self.daily = d

        # 4H candles: 09:15-13:15 and 13:15-15:30
        key4 = (df5["mins"] - 555) // 240
        g4 = df5.groupby([df5["date"], key4])
        h4 = pd.DataFrame({"high": g4["high"].max(), "low": g4["low"].min()}).reset_index()
        ph4, pl4 = pivots(h4["high"].values, h4["low"].values, k)
        conf4 = np.r_[h4["date"].values[k:], np.full(k, np.datetime64("NaT"), dtype="datetime64[ns]")]
        dph, dpl = pivots(d["high"].values, d["low"].values, k)
        dconf = np.r_[d.index.values[k:], np.full(k, np.datetime64("NaT"), dtype="datetime64[ns]")]
        # levels as (confirmation_date, price), sorted by confirmation date
        hi = [(conf4[i], h4["high"].iat[i]) for i in np.flatnonzero(ph4)] + \
             [(dconf[i], d["high"].iat[i]) for i in np.flatnonzero(dph)]
        lo = [(conf4[i], h4["low"].iat[i]) for i in np.flatnonzero(pl4)] + \
             [(dconf[i], d["low"].iat[i]) for i in np.flatnonzero(dpl)]
        self.hi = sorted([x for x in hi if not pd.isna(x[0])], key=lambda x: x[0])
        self.lo = sorted([x for x in lo if not pd.isna(x[0])], key=lambda x: x[0])
        self._hi_d = np.array([x[0] for x in self.hi], dtype="datetime64[ns]")
        self._lo_d = np.array([x[0] for x in self.lo], dtype="datetime64[ns]")

        pos = np.flatnonzero(df5["date"].values[1:] != df5["date"].values[:-1]) + 1
        starts, ends = np.r_[0, pos], np.r_[pos, len(df5)]
        self.slices = {df5["date"].iat[a]: (a, b) for a, b in zip(starts, ends)}
        self.plans = self._plans()

    def _levels(self, day, which: str, last: int = 40) -> np.ndarray:
        arr, dates = (self.hi, self._hi_d) if which == "hi" else (self.lo, self._lo_d)
        n = np.searchsorted(dates, np.datetime64(day), side="left")   # confirmed strictly before day
        return np.array([p for _, p in arr[max(0, n - last):n]])

    def _plans(self) -> dict:
        d, plans = self.daily, {}
        dates = d.index
        for i in range(1, len(d)):
            prev = d.iloc[i - 1]
            if pd.isna(prev["atr"]) or pd.isna(prev["ema50"]):
                continue
            day, pc, atr = dates[i], prev["close"], prev["atr"]
            lows, highs = self._levels(day, "lo"), self._levels(day, "hi")
            sup = lows[(lows < pc) & (lows >= pc - 1.5 * atr)]
            res = highs[(highs > pc) & (highs <= pc + 1.5 * atr)]
            plans[day] = {"trend": int(prev["trend"]), "atr": float(atr), "pc": float(pc),
                          "support": float(sup.max()) if len(sup) else np.nan,
                          "resistance": float(res.min()) if len(res) else np.nan,
                          "all_hi": highs, "all_lo": lows}
        return plans

    def trade(self, day, side: int, p: P):
        plan = self.plans.get(day)
        if plan is None or day not in self.slices:
            return None
        atr = plan["atr"]
        if side == 1:
            lvl = plan["support"]
            if np.isnan(lvl):
                return None
            z_bot, z_top = lvl - 0.25 * atr, lvl + p.zone_w * atr
            if z_top >= plan["pc"]:
                z_top = plan["pc"] - 0.05 * atr
        else:
            lvl = plan["resistance"]
            if np.isnan(lvl):
                return None
            z_top, z_bot = lvl + 0.25 * atr, lvl - p.zone_w * atr
            if z_bot <= plan["pc"]:
                z_bot = plan["pc"] + 0.05 * atr
        a, b = self.slices[day]
        f = self.df5
        o, h, l, c = (f[x].values[a:b] for x in ("open", "high", "low", "close"))
        mins = f["mins"].values[a:b]
        n = len(o)
        # quick reject: price never reached the zone today
        if (side == 1 and l.min() > z_top) or (side == -1 and h.max() < z_bot):
            return None
        # 15m candles built from 5m (indices into today's 5m arrays)
        b15 = (mins - 555) // 15
        cuts = np.flatnonzero(np.diff(b15)) + 1
        st15, en15 = np.r_[0, cuts], np.r_[cuts, n]           # [st, en) in 5m index
        O = o[st15]; C = c[en15 - 1]
        H = np.array([h[s:e].max() for s, e in zip(st15, en15)])
        L = np.array([l[s:e].min() for s, e in zip(st15, en15)])
        close_min = mins[en15 - 1] + 5
        touched, ext_i = False, -1
        entry_i = None
        for j in range(len(O)):
            if not touched:
                if (side == 1 and L[j] <= z_top) or (side == -1 and H[j] >= z_bot):
                    touched, ext_i = True, j
            if not touched:
                continue
            if (side == 1 and L[j] < L[ext_i]) or (side == -1 and H[j] > H[ext_i]):
                ext_i = j
            if (side == 1 and C[j] < z_bot) or (side == -1 and C[j] > z_top):
                return None                                     # level broken
            if close_min[j] < p.entry_start or close_min[j] > p.entry_end:
                continue
            if p.trigger == "MSS":
                if j < 3:
                    continue
                ok = C[j] > H[j - 3:j].max() if side == 1 else C[j] < L[j - 3:j].min()
            else:  # CISD
                k = ext_i
                if side == 1:
                    while k >= 0 and C[k] >= O[k]:
                        k -= 1
                    s_ = k
                    while k >= 0 and C[k] < O[k]:
                        k -= 1
                    lvl_c = O[k + 1] if s_ >= 0 else H[ext_i]
                    ok = C[j] > lvl_c and j >= ext_i
                else:
                    while k >= 0 and C[k] <= O[k]:
                        k -= 1
                    s_ = k
                    while k >= 0 and C[k] > O[k]:
                        k -= 1
                    lvl_c = O[k + 1] if s_ >= 0 else L[ext_i]
                    ok = C[j] < lvl_c and j >= ext_i
            if ok and en15[j] < n:
                entry_i, sweep = int(en15[j]), (L[ext_i] if side == 1 else H[ext_i])
                break
        if entry_i is None:
            return None

        slip = p.slippage_pct / 100
        raw = o[entry_i]
        stop = sweep * (1 - 0.0005) if side == 1 else sweep * (1 + 0.0005)
        risk_pct = (raw - stop) / raw * 100 * side
        if risk_pct <= 0 or risk_pct > p.sl_max_pct:
            return None
        if risk_pct < p.sl_min_pct:
            stop = raw * (1 - side * p.sl_min_pct / 100)
        entry = raw * (1 + side * slip)
        risk = abs(entry - stop)
        if p.target == "2R":
            target = entry + side * 2 * risk
        else:
            levels = plan["all_hi"] if side == 1 else plan["all_lo"]
            cand = levels[levels > entry] if side == 1 else levels[levels < entry]
            if not len(cand):
                return None
            lv = cand.min() if side == 1 else cand.max()
            rr = abs(lv - entry) / risk
            if rr < 1.5:
                return None
            target = entry + side * min(rr, 4.0) * risk
        qty = int(min(p.risk_rs / risk, p.max_notional / entry))
        if qty < 1:
            return None
        sl, be, reason, px, j = stop, False, None, None, entry_i
        for j in range(entry_i, n):
            if mins[j] >= p.exit_min:
                px, reason = o[j], "TIME"
                break
            if side == 1:
                if o[j] <= sl:
                    px, reason = o[j], "SL"
                elif l[j] <= sl:
                    px, reason = sl, "SL"
                elif h[j] >= target:
                    px, reason = max(o[j], target), "TARGET"
            else:
                if o[j] >= sl:
                    px, reason = o[j], "SL"
                elif h[j] >= sl:
                    px, reason = sl, "SL"
                elif l[j] <= target:
                    px, reason = min(o[j], target), "TARGET"
            if reason:
                break
            if p.breakeven and not be and side * (c[j] - entry) >= risk:
                sl, be = entry, True
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


def run(stocks: list[Stock], p: P, days) -> pd.DataFrame:
    out = []
    for day in days:
        trades = []
        for s in stocks:
            plan = s.plans.get(day)
            if plan is None:
                continue
            sides = []
            if p.use_trend:
                if plan["trend"] == 1:
                    sides = [1]
                elif plan["trend"] == -1:
                    sides = [-1]
            else:
                sides = [1, -1]
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


def stats(t: pd.DataFrame) -> dict:
    if t.empty:
        return {"trades": 0, "net": 0, "win_rate": 0.0, "pf": 0.0, "avg_R": 0.0, "max_dd": 0, "charges": 0}
    w, l = t[t["net"] > 0]["net"].sum(), -t[t["net"] <= 0]["net"].sum()
    eq = t["net"].cumsum()
    return {"trades": len(t), "net": round(t["net"].sum()), "win_rate": round((t["net"] > 0).mean() * 100, 1),
            "pf": round(w / l, 2) if l > 0 else 99.0, "avg_R": round(t["R"].mean(), 3),
            "max_dd": round((eq - eq.cummax()).min()), "charges": round(t["charges"].sum())}


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
                stocks.append(Stock(name, load_csv(pth)))
            except Exception as exc:  # noqa: BLE001
                print("skip", name, exc)
    print(f"Loaded {len(stocks)}/{len(wanted)} stocks; missing: {sorted(wanted - {s.sym for s in stocks})}")
    days = sorted(set().union(*(set(s.plans) for s in stocks)))
    cut = int(len(days) * args.is_frac)
    is_days, oos_days = days[:cut], days[cut:]
    f = lambda d: pd.Timestamp(d).date()  # noqa: E731
    print(f"In-sample {f(is_days[0])} -> {f(is_days[-1])} ({len(is_days)}) | "
          f"Out-of-sample {f(oos_days[0])} -> {f(oos_days[-1])} ({len(oos_days)})")

    rows = []
    for trig, tgt, be, zw in itertools.product(["MSS", "CISD"], ["2R", "level"], [True, False], [0.25, 0.5]):
        p = P(trigger=trig, target=tgt, breakeven=be, zone_w=zw)
        si, so = stats(run(stocks, p, is_days)), stats(run(stocks, p, oos_days))
        rows.append({"trigger": trig, "target": tgt, "breakeven": be, "zone_w": zw,
                     **{f"IS_{k}": v for k, v in si.items()}, **{f"OOS_{k}": v for k, v in so.items()}})
        print(f"{trig:4s} {tgt:5s} be={be!s:5s} zone={zw} | IS {si['net']:>8,} pf {si['pf']:.2f} n={si['trades']:>4}"
              f" | OOS {so['net']:>8,} pf {so['pf']:.2f} n={so['trades']:>4}", flush=True)
    g = pd.DataFrame(rows)
    g.to_csv(out / "grid.csv", index=False)
    elig = g[g["IS_trades"] >= 60]
    br = (elig if len(elig) else g).sort_values(["IS_pf", "IS_net"], ascending=False).iloc[0]
    best = P(trigger=br["trigger"], target=br["target"], breakeven=bool(br["breakeven"]), zone_w=float(br["zone_w"]))
    chosen = {"trigger": best.trigger, "target": best.target, "breakeven": best.breakeven, "zone_w": best.zone_w}
    print("Chosen on in-sample:", chosen)

    variants = {"FULL (1D trend + zone + 15m confirm)": best,
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
    lines = ["# MTF Roadmap (1D -> 4H -> 15M) - backtest", "",
             f"Stocks: {len(stocks)} | In-sample {f(is_days[0])} -> {f(is_days[-1])} | "
             f"Out-of-sample {f(oos_days[0])} -> {f(oos_days[-1])}",
             f"Chosen params (in-sample only): {json.dumps(chosen)}", "",
             "## Variants (Rs, Rs 1,000 risk per trade, after charges)", "",
             abl[["variant", "IS_trades", "IS_net", "IS_win_rate", "IS_pf", "OOS_trades", "OOS_net",
                  "OOS_win_rate", "OOS_pf", "OOS_avg_R", "OOS_max_dd"]].to_markdown(index=False)]
    if not t.empty:
        oos_set = {f(d) for d in oos_days}
        t["period"] = np.where(t["date"].isin(oos_set), "OOS", "IS")
        t.to_csv(out / "trades_best.csv", index=False)
        lines += ["", "## Exit reasons", "",
                  t.groupby("period")["reason"].value_counts().unstack(fill_value=0).to_markdown(),
                  "", "## By month", "",
                  t.groupby(pd.to_datetime(t["date"]).dt.to_period("M").astype(str))["net"]
                  .agg(["count", "sum"]).round(0).to_markdown()]
    lines += ["", "## Full grid", "", g.to_markdown(index=False)]
    (out / "summary.md").write_text("\n".join(lines))
    print("\n".join(lines[:12]))


if __name__ == "__main__":
    main()
