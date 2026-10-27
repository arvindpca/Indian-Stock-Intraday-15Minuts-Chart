#!/usr/bin/env python3
"""
"Strong Sector Pullback" intraday strategy - backtest on saved 5-min data.

Rules (LONG shown, SHORT is the mirror image)
---------------------------------------------
Decision time T0 (default 09:45) - only candles closed before T0 are used.
1. Market  : Nifty > prev close by >= 0.2 % AND Nifty > today's open  -> LONG day
             (mirror -> SHORT day, otherwise no trade today)
2. Sector  : sector % change = median % change (vs prev close) of its stocks.
             Pick the strongest sector(s) that are also stronger than Nifty.
3. Stock   : in that sector, change +1.0 % .. +3.5 %, relative volume >= 1.5
             (volume 09:15->T0 vs average of same window, last 10 days),
             price above VWAP, liquid (median daily turnover >= 5 Cr).
4. Entry   : wait for a pullback after T0: candle low touches EMA20 (5-min),
             closes back above EMA20 and above VWAP, green candle.
             Buy at the NEXT candle open (no look-ahead).
5. Risk    : SL just below the low of the last 3 candles (0.3 % .. 1.5 % of price),
             target = RR x risk, optional move SL to entry after +1R,
             exit at the 15:00 candle open. Same candle hits SL and target -> SL.
             Rs 1,000 risk per trade, max Rs 2 L per position,
             max 3 trades a day, stop for the day after 2 losses.
Costs: Angel One intraday charges + 0.03 % slippage per fill.

Parameters are chosen on the first 2/3 of the days (in-sample) and then
reported on the last 1/3 (out-of-sample) that was never used for choosing.
"""
from __future__ import annotations

import argparse
import itertools
import json
from dataclasses import asdict, dataclass, replace
from datetime import time as dtime
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
BAR = 5


# --------------------------------------------------------------------------- #
# Params
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Params:
    decision_time: str = "09:45"
    market_min_pct: float = 0.2
    use_market: bool = True
    use_sector: bool = True
    top_sectors: int = 1
    min_sector_stocks: int = 3
    stock_min_pct: float = 1.0
    stock_max_pct: float = 3.5
    min_rvol: float = 1.5
    rvol_days: int = 10
    min_turnover_cr: float = 5.0
    max_candidates: int = 3
    use_pullback: bool = True
    touch_tol_pct: float = 0.10
    ema_period: int = 20
    last_entry: str = "13:30"
    exit_time: str = "15:00"
    sl_min_pct: float = 0.3
    sl_max_pct: float = 1.5
    rr: float = 2.0
    breakeven: bool = True
    risk_rs: float = 1000.0
    max_notional: float = 200_000.0
    max_trades_day: int = 3
    max_losses_day: int = 2
    slippage_pct: float = 0.03


def charges(buy_v: float, sell_v: float) -> float:
    """Angel One equity intraday (same rates as research/config.yaml)."""
    def brok(v: float) -> float:
        return max(5.0, min(20.0, v * 0.10 / 100))
    b = brok(buy_v) + brok(sell_v)
    t = buy_v + sell_v
    exch, sebi = t * 0.0030699 / 100, t * 10 / 1e7
    return b + sell_v * 0.025 / 100 + exch + sebi + buy_v * 0.003 / 100 + (b + exch + sebi) * 0.18


def tmin(s: str) -> int:
    h, m = map(int, s.split(":"))
    return h * 60 + m


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
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
    return df[(t >= dtime(9, 15)) & (t < dtime(15, 30))]


def symbol_from_path(p: Path) -> str:
    n = p.name
    for s in (".csv.gz", ".csv"):
        n = n.removesuffix(s)
    return n.removesuffix("_5m")


def prepare(df: pd.DataFrame, ema_period: int) -> pd.DataFrame:
    """Causal per-bar features."""
    f = df.copy()
    f["date"] = f.index.date
    f["mins"] = f.index.hour * 60 + f.index.minute
    tp = (f["high"] + f["low"] + f["close"]) / 3
    g = f.groupby("date")
    cv = g["volume"].cumsum()
    f["vwap"] = (tp * f["volume"]).groupby(f["date"]).cumsum() / cv.replace(0, np.nan)
    f["vwap"] = f["vwap"].fillna(tp)
    f["ema"] = f["close"].ewm(span=ema_period, adjust=False, min_periods=ema_period).mean()
    f["cumvol"] = cv
    f["day_open"] = g["open"].transform("first")
    day_close = g["close"].last()
    f["prev_close"] = f["date"].map(day_close.shift(1))
    return f


class Market:
    """All symbols + Nifty, plus daily snapshot tables used for selection."""

    def __init__(self, stocks: dict[str, pd.DataFrame], nifty: pd.DataFrame, sectors: dict[str, str],
                 ema_period: int):
        self.f = {s: prepare(d, ema_period) for s, d in stocks.items()}
        self.nifty = prepare(nifty, ema_period)
        self.sectors = sectors
        self.tradable: set[str] | None = None   # None = every loaded stock can be traded
        days = sorted(set(self.nifty["date"]))
        self.days = [d for d in days if (self.nifty["date"] == d).sum() >= 60]
        # fast slicing per (symbol, day)
        self.slices: dict[str, dict] = {}
        for s, f in self.f.items():
            pos = np.flatnonzero(f["date"].values[1:] != f["date"].values[:-1]) + 1
            starts = np.r_[0, pos]
            ends = np.r_[pos, len(f)]
            self.slices[s] = {f["date"].iat[a]: (a, b) for a, b in zip(starts, ends)}
        # turnover filter (prior 20 days, no look-ahead)
        self.turnover_ok = {}
        for s, f in self.f.items():
            to = (f["close"] * f["volume"]).groupby(f["date"]).sum() / 1e7
            self.turnover_ok[s] = to.shift(1).rolling(20, min_periods=10).median()
        self._snap_cache: dict[int, pd.DataFrame] = {}

    def snapshot(self, decision_min: int, rvol_days: int) -> pd.DataFrame:
        """Rows (date, symbol): chg %, rvol, above_vwap, turnover_cr - using the
        candle that closes exactly at decision time."""
        if decision_min in self._snap_cache:
            return self._snap_cache[decision_min]
        last_bar = decision_min - BAR
        rows = []
        for s, f in self.f.items():
            snap = f[f["mins"] == last_bar].set_index("date")
            if snap.empty:
                continue
            base = snap["cumvol"].shift(1).rolling(rvol_days, min_periods=5).mean()
            out = pd.DataFrame({
                "chg": (snap["close"] / snap["prev_close"] - 1) * 100,
                "rvol": snap["cumvol"] / base,
                "close": snap["close"], "vwap": snap["vwap"],
                "turnover": self.turnover_ok[s].reindex(snap.index),
            })
            out["symbol"] = s
            out["sector"] = self.sectors.get(s, "UNKNOWN")
            rows.append(out)
        snap_all = pd.concat(rows)
        snap_all.index.name = "date"
        self._snap_cache[decision_min] = snap_all
        return snap_all

    def nifty_state(self, decision_min: int) -> pd.DataFrame:
        n = self.nifty[self.nifty["mins"] == decision_min - BAR].set_index("date")
        return pd.DataFrame({"chg": (n["close"] / n["prev_close"] - 1) * 100,
                             "above_open": n["close"] > n["day_open"],
                             "below_open": n["close"] < n["day_open"]})


# --------------------------------------------------------------------------- #
# Strategy
# --------------------------------------------------------------------------- #
def pick_candidates(m: Market, p: Params, day, snap_day: pd.DataFrame, nifty_row) -> tuple[int, list[str]]:
    """Returns (direction +1/-1/0, candidate symbols)."""
    nchg = nifty_row["chg"]
    if p.use_market:
        if nchg >= p.market_min_pct and nifty_row["above_open"]:
            side = 1
        elif nchg <= -p.market_min_pct and nifty_row["below_open"]:
            side = -1
        else:
            return 0, []
    else:
        side = 1 if nchg >= 0 else -1

    s = snap_day.dropna(subset=["chg", "rvol", "turnover"])
    s = s[s["turnover"] >= p.min_turnover_cr]
    if p.use_sector:
        known = s[s["sector"] != "UNKNOWN"]
        sec = known.groupby("sector")["chg"].agg(["median", "count"])
        sec = sec[sec["count"] >= p.min_sector_stocks]
        if side == 1:
            sec = sec[sec["median"] > max(nchg, 0)].sort_values("median", ascending=False)
        else:
            sec = sec[sec["median"] < min(nchg, 0)].sort_values("median")
        chosen = list(sec.index[: p.top_sectors])
        if not chosen:
            return side, []
        s = s[s["sector"].isin(chosen)]

    if m.tradable is not None:
        s = s[s["symbol"].isin(m.tradable)]
    if side == 1:
        c = s[(s["chg"] >= p.stock_min_pct) & (s["chg"] <= p.stock_max_pct)
              & (s["rvol"] >= p.min_rvol) & (s["close"] > s["vwap"])]
        c = c.sort_values("chg", ascending=False)
    else:
        c = s[(s["chg"] <= -p.stock_min_pct) & (s["chg"] >= -p.stock_max_pct)
              & (s["rvol"] >= p.min_rvol) & (s["close"] < s["vwap"])]
        c = c.sort_values("chg")
    return side, list(c["symbol"].iloc[: p.max_candidates])


def first_trade(m: Market, p: Params, sym: str, day, side: int, decision_min: int):
    """Simulate the first valid trade of `sym` on `day`. Returns dict or None."""
    a, b = m.slices[sym].get(day, (0, 0))
    if b - a < 60:
        return None
    f = m.f[sym]
    o = f["open"].values[a:b]; h = f["high"].values[a:b]; lo = f["low"].values[a:b]
    c = f["close"].values[a:b]; ema = f["ema"].values[a:b]; vw = f["vwap"].values[a:b]
    mins = f["mins"].values[a:b]
    last_entry, exit_t = tmin(p.last_entry), tmin(p.exit_time)
    tol = p.touch_tol_pct / 100
    slip = p.slippage_pct / 100

    entry_i = None
    stop = None
    if p.use_pullback:
        for t in range(len(c) - 1):
            if mins[t] < decision_min or mins[t] > last_entry:
                continue
            if np.isnan(ema[t]):
                continue
            if side == 1:
                trig = lo[t] <= ema[t] * (1 + tol) and c[t] > ema[t] and c[t] > vw[t] and c[t] > o[t]
            else:
                trig = h[t] >= ema[t] * (1 - tol) and c[t] < ema[t] and c[t] < vw[t] and c[t] < o[t]
            if not trig:
                continue
            ent = o[t + 1]
            sl = (lo[max(0, t - 2): t + 1].min() * (1 - 0.0005) if side == 1
                  else h[max(0, t - 2): t + 1].max() * (1 + 0.0005))
            risk_pct = abs(ent - sl) / ent * 100
            if (side == 1 and sl >= ent) or (side == -1 and sl <= ent) or risk_pct > p.sl_max_pct:
                continue
            entry_i, stop = t + 1, sl
            break
    else:
        idx = np.flatnonzero(mins == decision_min)
        if len(idx):
            entry_i = int(idx[0])
            ent = o[entry_i]
            stop = lo[:entry_i].min() if side == 1 else h[:entry_i].max()
            if abs(ent - stop) / ent * 100 > p.sl_max_pct:
                stop = ent * (1 - side * p.sl_max_pct / 100)
    if entry_i is None:
        return None

    raw_entry = o[entry_i]
    if abs(raw_entry - stop) / raw_entry * 100 < p.sl_min_pct:
        stop = raw_entry * (1 - side * p.sl_min_pct / 100)
    entry = raw_entry * (1 + side * slip)
    risk = abs(entry - stop)
    target = entry + side * p.rr * risk
    qty = int(min(p.risk_rs / risk, p.max_notional / entry))
    if qty < 1:
        return None

    sl, be_done, exit_px, reason, j = stop, False, None, None, entry_i
    for j in range(entry_i, len(c)):
        if mins[j] >= exit_t:
            exit_px, reason = o[j], "TIME"
            break
        if side == 1:
            if o[j] <= sl:
                exit_px, reason = o[j], "SL"
            elif lo[j] <= sl:
                exit_px, reason = sl, "SL"
            elif h[j] >= target:
                exit_px, reason = max(o[j], target), "TARGET"
        else:
            if o[j] >= sl:
                exit_px, reason = o[j], "SL"
            elif h[j] >= sl:
                exit_px, reason = sl, "SL"
            elif lo[j] <= target:
                exit_px, reason = min(o[j], target), "TARGET"
        if reason:
            break
        if p.breakeven and not be_done and side * (c[j] - entry) >= risk:
            sl, be_done = entry, True          # takes effect from next candle
    if reason is None:
        exit_px, reason = c[-1], "TIME"
    if reason == "SL" and be_done:
        reason = "BREAKEVEN"
    exit_px = exit_px * (1 - side * slip)
    gross = (exit_px - entry) * qty * side
    buy_v, sell_v = (entry * qty, exit_px * qty) if side == 1 else (exit_px * qty, entry * qty)
    ch = charges(buy_v, sell_v)
    return {"date": day, "symbol": sym, "sector": m.sectors.get(sym, "UNKNOWN"),
            "side": "LONG" if side == 1 else "SHORT",
            "entry_time": f"{mins[entry_i] // 60:02d}:{mins[entry_i] % 60:02d}",
            "exit_time": f"{mins[j] // 60:02d}:{mins[j] % 60:02d}",
            "entry_min": int(mins[entry_i]), "exit_min": int(mins[j]),
            "qty": qty, "entry": round(entry, 2), "stop": round(stop, 2), "target": round(target, 2),
            "exit": round(exit_px, 2), "reason": reason, "gross": round(gross, 2),
            "charges": round(ch, 2), "net": round(gross - ch, 2), "R": round((gross - ch) / p.risk_rs, 2)}


def run(m: Market, p: Params, days) -> pd.DataFrame:
    dmin = tmin(p.decision_time)
    snap = m.snapshot(dmin, p.rvol_days)
    nifty = m.nifty_state(dmin)
    snap_by_day = dict(tuple(snap.groupby(level=0)))
    out = []
    for day in days:
        if day not in nifty.index or day not in snap_by_day or pd.isna(nifty.loc[day, "chg"]):
            continue
        side, cands = pick_candidates(m, p, day, snap_by_day[day], nifty.loc[day])
        if not side or not cands:
            continue
        trades = [t for s in cands if (t := first_trade(m, p, s, day, side, dmin))]
        trades.sort(key=lambda t: t["entry_min"])
        taken = []
        for t in trades:
            losses = sum(1 for x in taken if x["net"] < 0 and x["exit_min"] <= t["entry_min"])
            if len(taken) >= p.max_trades_day or losses >= p.max_losses_day:
                break
            taken.append(t)
        out += taken
    return pd.DataFrame(out)


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def stats(t: pd.DataFrame) -> dict:
    if t.empty:
        return {"trades": 0, "net": 0.0, "win_rate": 0.0, "pf": 0.0, "avg_R": 0.0, "max_dd": 0.0,
                "charges": 0.0, "days": 0}
    w, l = t[t["net"] > 0]["net"].sum(), -t[t["net"] <= 0]["net"].sum()
    eq = t["net"].cumsum()
    return {"trades": len(t), "days": t["date"].nunique(), "net": round(t["net"].sum()),
            "win_rate": round((t["net"] > 0).mean() * 100, 1),
            "pf": round(w / l, 2) if l > 0 else 99.0, "avg_R": round(t["R"].mean(), 3),
            "max_dd": round((eq - eq.cummax()).min()), "charges": round(t["charges"].sum())}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--symbols-file", required=True)
    ap.add_argument("--sector-map", default=str(HERE / "sector_map.csv"))
    ap.add_argument("--trade-symbols-file", default=None,
                    help="trade only these; all --symbols-file stocks still count for sector strength")
    ap.add_argument("--out", required=True)
    ap.add_argument("--is-frac", type=float, default=2 / 3)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    wanted = {x.strip().upper() for x in Path(args.symbols_file).read_text().splitlines() if x.strip()}
    sectors = {}
    if Path(args.sector_map).exists():
        sm = pd.read_csv(args.sector_map)
        sectors = dict(zip(sm["symbol"].str.upper(), sm["sector"]))
    stocks, nifty = {}, None
    for pth in sorted(Path(args.data_dir).iterdir()):
        if not pth.name.endswith((".csv", ".csv.gz")):
            continue
        sym = symbol_from_path(pth)
        if sym == "NIFTY":
            nifty = load_csv(pth)
        elif sym in wanted:
            try:
                stocks[sym] = load_csv(pth)
            except Exception as exc:  # noqa: BLE001
                print("skip", sym, exc)
    if nifty is None:
        raise SystemExit("NIFTY data missing")
    cover = sum(s in sectors for s in stocks)
    print(f"Loaded {len(stocks)} stocks, sector known for {cover}")
    sec_counts = pd.Series({s: sectors.get(s, "UNKNOWN") for s in stocks}).value_counts()
    print(sec_counts.to_string())

    m = Market(stocks, nifty, sectors, Params().ema_period)
    if args.trade_symbols_file:
        tr = {x.strip().upper() for x in Path(args.trade_symbols_file).read_text().splitlines() if x.strip()}
        m.tradable = tr & set(stocks)
        print(f"Tradable stocks ({len(m.tradable)}): {', '.join(sorted(m.tradable))}")
        missing = tr - set(stocks)
        if missing:
            print("No data for:", ', '.join(sorted(missing)))
    days = m.days
    cut = int(len(days) * args.is_frac)
    is_days, oos_days = days[:cut], days[cut:]
    print(f"\nIn-sample : {is_days[0]} -> {is_days[-1]} ({len(is_days)} days)")
    print(f"Out-sample: {oos_days[0]} -> {oos_days[-1]} ({len(oos_days)} days)\n")

    # ---- small grid, chosen on in-sample only
    grid = list(itertools.product(["09:45", "10:15"], [1.5, 2.0], [True, False], [1, 2]))
    rows = []
    for dt, rr, be, ts in grid:
        p = Params(decision_time=dt, rr=rr, breakeven=be, top_sectors=ts)
        s_is, s_oos = stats(run(m, p, is_days)), stats(run(m, p, oos_days))
        rows.append({"decision_time": dt, "rr": rr, "breakeven": be, "top_sectors": ts,
                     **{f"IS_{k}": v for k, v in s_is.items()},
                     **{f"OOS_{k}": v for k, v in s_oos.items()}})
        print(f"{dt} rr={rr} be={be} sectors={ts} | IS net {s_is['net']:>9,} pf {s_is['pf']:.2f} "
              f"n={s_is['trades']:>4} | OOS net {s_oos['net']:>9,} pf {s_oos['pf']:.2f} n={s_oos['trades']:>4}")
    g = pd.DataFrame(rows)
    g.to_csv(out / "grid.csv", index=False)
    elig = g[g["IS_trades"] >= 60]
    best_row = (elig if len(elig) else g).sort_values(["IS_pf", "IS_net"], ascending=False).iloc[0]
    best = Params(decision_time=best_row["decision_time"], rr=float(best_row["rr"]),
                  breakeven=bool(best_row["breakeven"]), top_sectors=int(best_row["top_sectors"]))
    print("\nChosen on in-sample:", {k: getattr(best, k) for k in
                                     ("decision_time", "rr", "breakeven", "top_sectors")})

    # ---- ablation: what does each filter add?
    variants = {
        "FULL strategy": best,
        "without market filter": replace(best, use_market=False),
        "without sector filter": replace(best, use_sector=False),
        "without pullback entry": replace(best, use_pullback=False),
    }
    abl = []
    for name, p in variants.items():
        r = {"variant": name}
        for label, dd in (("IS", is_days), ("OOS", oos_days), ("ALL", days)):
            r.update({f"{label}_{k}": v for k, v in stats(run(m, p, dd)).items()})
        abl.append(r)
    abl = pd.DataFrame(abl)
    abl.to_csv(out / "ablation.csv", index=False)

    t_all = run(m, best, days)
    t_all["period"] = np.where(t_all["date"].isin(set(oos_days)), "OOS", "IS")
    t_all.to_csv(out / "trades_best.csv", index=False)

    lines = ["# Strong Sector Pullback - backtest", "",
             f"Stocks: {len(stocks)} (sector known: {cover}) | Data: {days[0]} -> {days[-1]}",
             f"Tradable: {'all' if m.tradable is None else ', '.join(sorted(m.tradable))}",
             f"In-sample {is_days[0]} -> {is_days[-1]} | Out-of-sample {oos_days[0]} -> {oos_days[-1]}",
             f"Chosen params (in-sample): {json.dumps({k: getattr(best, k) for k in ('decision_time', 'rr', 'breakeven', 'top_sectors')})}",
             "", "## Ablation (Rs, Rs 1,000 risk per trade, after charges)", "",
             abl[["variant", "IS_trades", "IS_net", "IS_pf", "OOS_trades", "OOS_net", "OOS_win_rate",
                  "OOS_pf", "OOS_avg_R", "OOS_max_dd", "ALL_net"]].to_markdown(index=False)]
    if not t_all.empty:
        t_all["month"] = pd.to_datetime(t_all["date"]).dt.to_period("M").astype(str)
        mon = t_all.groupby("month")["net"].agg(["count", "sum"]).round(0)
        side = t_all.groupby(["period", "side"])["net"].agg(["count", "sum", lambda s: (s > 0).mean()])
        side.columns = ["trades", "net", "win_rate"]
        reasons = t_all.groupby("period")["reason"].value_counts().unstack(fill_value=0)
        sec = t_all.groupby("sector")["net"].agg(["count", "sum"]).sort_values("sum")
        lines += ["", "## Best params - by side", "", side.round(2).to_markdown(),
                  "", "## Exit reasons", "", reasons.to_markdown(),
                  "", "## By month", "", mon.to_markdown(),
                  "", "## By sector", "", sec.round(0).to_markdown()]
    lines += ["", "## Full grid (chosen by IS_pf)", "", g.to_markdown(index=False)]
    (out / "summary.md").write_text("\n".join(lines))
    print("\n".join(lines[:16]))


if __name__ == "__main__":
    main()
