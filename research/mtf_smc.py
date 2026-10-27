"""Multi-timeframe SMC combo backtest (4H direction -> 1H liquidity -> 15m BOS/ChoCH -> 5m entry).

Rules (mechanical, decided BEFORE looking at results - no optimisation):
  4H  : trend from structure. close > last confirmed 4H swing high -> UP, close < last swing low -> DOWN.
  1H  : liquidity = confirmed 1H swing lows (for longs) / swing highs (for shorts), max age `liq_age_1h` bars.
        A 15m candle whose wick goes beyond such a level = "liquidity hunted" (sweep). Level is then used up.
  15m : after the sweep, ChoCH = 15m CLOSE above the last confirmed 15m swing high that formed before the
        sweep's extreme (shorts: mirror). Setup expires after `setup_max_15m` 15m candles.
  5m  : trade placed on 5m:
          MKT -> next 5m candle open after the ChoCH candle closes
          LIM -> limit at 50% of the leg (sweep extreme -> high since sweep), valid till entry_end, cancelled
                 if the stop is touched first
        SL = sweep extreme (structure stop). Target = RR x risk. Square-off at session.square_off.
  Variants: 4H filter ON/OFF x entry MKT/LIM x RR 1.5/2/3  (12 total, all reported).

No lookahead: a higher-TF candle / pivot is used only after the 5m candle that completes it has closed.
Costs: same Angel One intraday charges + slippage as research/engine.py.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd

from research.common import ROOT, get_logger, load_config, load_universe, write_json
from research.costs import intraday_charges
from research.data_quality import validate_universe

P = dict(k4=2, k1=2, k15=2, liq_age_1h=70, setup_max_15m=26, min_risk_pct=0.15, max_risk_pct=3.0,
         max_trades_day=2, rrs=[1.5, 2.0, 3.0])


# ---------------------------------------------------------------- helpers
def resample(df5: pd.DataFrame, n: int) -> pd.DataFrame:
    """Session-anchored candles (09:15 start). avail = close time of the last 5m bar inside."""
    m = (df5["datetime"].dt.hour * 60 + df5["datetime"].dt.minute) - 555
    key = [df5["datetime"].dt.normalize(), m // n]
    g = df5.groupby(key, sort=True)
    out = pd.DataFrame({"start": g["datetime"].first(), "open": g["open"].first(), "high": g["high"].max(),
                        "low": g["low"].min(), "close": g["close"].last(),
                        "avail": g["datetime"].last() + pd.Timedelta(minutes=5)}).reset_index(drop=True)
    return out


def pivots(h: np.ndarray, l: np.ndarray, k: int):
    n = len(h)
    ph = np.zeros(n, bool)
    pl = np.zeros(n, bool)
    for i in range(k, n - k):
        w_h, w_l = h[i - k:i + k + 1], l[i - k:i + k + 1]
        ph[i] = h[i] == w_h.max() and (w_h == h[i]).sum() == 1
        pl[i] = l[i] == w_l.min() and (w_l == l[i]).sum() == 1
    return ph, pl


def trend_4h(c4: pd.DataFrame, k: int) -> pd.DataFrame:
    h, l, c = c4["high"].to_numpy(), c4["low"].to_numpy(), c4["close"].to_numpy()
    ph, pl = pivots(h, l, k)
    sh = sl = np.nan
    tr = np.zeros(len(c), np.int8)
    state = 0
    for b in range(len(c)):
        i = b - 1 - k                       # pivot at i is confirmed at close of i+k (<= b-1)
        if i >= 0:
            if ph[i]:
                sh = h[i]
            if pl[i]:
                sl = l[i]
        if np.isfinite(sh) and c[b] > sh:
            state = 1
        elif np.isfinite(sl) and c[b] < sl:
            state = -1
        tr[b] = state
    return pd.DataFrame({"avail": c4["avail"], "trend": tr})


# ---------------------------------------------------------------- signals
def find_signals(df5: pd.DataFrame, cfg: dict) -> list[dict]:
    c15, c60, c240 = resample(df5, 15), resample(df5, 60), resample(df5, 240)
    tr = trend_4h(c240, P["k4"])
    # trend known at each 15m candle close
    t15 = pd.merge_asof(c15[["avail"]], tr, on="avail", direction="backward")["trend"].fillna(0).to_numpy()

    h1, l1 = c60["high"].to_numpy(), c60["low"].to_numpy()
    ph1, pl1 = pivots(h1, l1, P["k1"])
    av1 = c60["avail"].to_numpy()
    k1 = P["k1"]
    # level list: (confirm_time, level, pivot_index)
    lv_low = [(av1[i + k1], l1[i], i) for i in np.nonzero(pl1)[0] if i + k1 < len(av1)]
    lv_high = [(av1[i + k1], h1[i], i) for i in np.nonzero(ph1)[0] if i + k1 < len(av1)]
    start1 = c60["start"].to_numpy()

    H, L, C = c15["high"].to_numpy(), c15["low"].to_numpy(), c15["close"].to_numpy()
    av15, st15 = c15["avail"].to_numpy(), c15["start"].to_numpy()
    ph15, pl15 = pivots(H, L, P["k15"])
    k15 = P["k15"]

    js = np.timedelta64(9 * 60 + 30, "m")
    je = np.timedelta64(14 * 60 + 40, "m")
    sigs = []
    for side, levels, piv in ((1, lv_low, ph15), (-1, lv_high, pl15)):
        used = np.zeros(len(levels), bool)
        lv_t = np.array([x[0] for x in levels], dtype="datetime64[ns]")
        lv_p = np.array([x[1] for x in levels])
        lv_i = np.array([x[2] for x in levels])
        piv_idx = np.nonzero(piv)[0]
        setup = None
        for q in range(len(C)):
            # 1) sweep of an active 1H level by this 15m candle (level must be known before candle start)
            if len(lv_p):
                # age limit: pivot within last liq_age_1h hourly candles of this candle's start
                j_now = np.searchsorted(start1, st15[q], side="right") - 1
                act = (~used) & (lv_t <= st15[q]) & (lv_i >= j_now - P["liq_age_1h"])
                hit = act & ((L[q] < lv_p) if side == 1 else (H[q] > lv_p))
                if hit.any():
                    used |= hit
                    ext = L[q] if side == 1 else H[q]
                    setup = {"q0": q, "ext": ext, "ext_i": q, "lvl": float(lv_p[hit][0])}
            if setup is None:
                continue
            if q - setup["q0"] > P["setup_max_15m"]:
                setup = None
                continue
            if side == 1 and L[q] < setup["ext"]:
                setup["ext"], setup["ext_i"] = L[q], q
            if side == -1 and H[q] > setup["ext"]:
                setup["ext"], setup["ext_i"] = H[q], q
            # 2) ChoCH level: last confirmed 15m swing (opposite) formed before the extreme
            ok = piv_idx[(piv_idx < setup["ext_i"]) & (piv_idx + k15 <= q)]
            if not len(ok):
                continue
            pi = ok[-1]
            lvl = H[pi] if side == 1 else L[pi]
            brk = C[q] > lvl if side == 1 else C[q] < lvl
            if not brk:
                continue
            sig_bar = av15[q] - np.timedelta64(5, "m")        # last 5m candle of this 15m candle
            tod = sig_bar - sig_bar.astype("datetime64[D]")
            if tod < js or tod > je:
                setup = None
                continue
            leg_ext = (H[setup["ext_i"]:q + 1].max() if side == 1 else L[setup["ext_i"]:q + 1].min())
            sigs.append({"side": side, "sig_time": av15[q], "sl": setup["ext"], "leg_ext": leg_ext,
                         "trend": int(t15[q]), "choch_lvl": lvl, "liq_lvl": setup["lvl"]})
            setup = None
    return sorted(sigs, key=lambda x: x["sig_time"])


# ---------------------------------------------------------------- simulation
def simulate(df5: pd.DataFrame, sigs: list[dict], mode: str, rr: float, use4h: bool, cfg: dict, sym: str):
    t = df5["datetime"].to_numpy()
    O, H, L, Cc = (df5[c].to_numpy() for c in ("open", "high", "low", "close"))
    day = df5["datetime"].dt.normalize().to_numpy()
    tod = (df5["datetime"].dt.hour * 60 + df5["datetime"].dt.minute).to_numpy()
    avgv = df5["volume"].rolling(20, min_periods=5).mean().shift(1).to_numpy()
    ntd = df5["no_trade_day"].to_numpy()
    sq_h, sq_m = map(int, cfg["session"]["square_off"].split(":"))
    sq = sq_h * 60 + sq_m
    eh, em = map(int, cfg["session"]["entry_end"].split(":"))
    last_entry = eh * 60 + em + 5
    risk_cfg, costs = cfg["risk"], cfg["costs"]
    slip = costs["slippage_pct"] / 100

    trades, busy_until = [], np.datetime64("1970-01-01")
    per_day: dict = {}
    for s in sigs:
        if use4h and s["trend"] != s["side"]:
            continue
        d = s["side"]
        i0 = np.searchsorted(t, s["sig_time"])                # first 5m candle after the 15m close
        if i0 >= len(t) or day[i0] != np.datetime64(s["sig_time"], "D") or ntd[i0]:
            continue
        if t[i0] < busy_until or per_day.get(day[i0], 0) >= P["max_trades_day"]:
            continue
        sl = s["sl"]
        # ---- entry
        ie, E = None, None
        if mode == "MKT":
            ie, E = i0, O[i0]
        else:
            lim = sl + 0.5 * (s["leg_ext"] - sl)
            i = i0
            while i < len(t) and day[i] == day[i0] and tod[i] <= last_entry:
                if (d == 1 and O[i] <= sl) or (d == -1 and O[i] >= sl):
                    break                                    # opened through stop -> cancel
                if (d == 1 and L[i] <= lim) or (d == -1 and H[i] >= lim):
                    ie, E = i, (min(O[i], lim) if d == 1 else max(O[i], lim))
                    break
                if (d == 1 and L[i] <= sl) or (d == -1 and H[i] >= sl):
                    break
                i += 1
        if ie is None or tod[ie] > last_entry:
            continue
        R = (E - sl) * d
        if not np.isfinite(R) or R <= 0 or not (P["min_risk_pct"] <= 100 * R / E <= P["max_risk_pct"]):
            continue
        tp = E + d * rr * R
        # ---- exit (SL checked first in the same candle = conservative)
        ix, X, why = None, None, None
        i = ie
        while i < len(t) and day[i] == day[ie]:
            if tod[i] >= sq:
                ix, X, why = i, O[i], "EOD"
                break
            hit_sl = (L[i] <= sl) if d == 1 else (H[i] >= sl)
            hit_tp = (H[i] >= tp) if d == 1 else (L[i] <= tp)
            if i == ie and mode == "LIM":
                # entry candle: only stop can be judged safely (we don't know path) -> stop if touched
                hit_tp = False
            if hit_sl:
                X = (min(O[i], sl) if d == 1 else max(O[i], sl)) if i > ie else sl
                ix, why = i, "SL"
                break
            if hit_tp:
                ix, X, why = i, tp, "TP"
                break
            i += 1
        if ix is None:                                       # day ended before square-off candle
            ix = i - 1
            X, why = Cc[ix], "EOD"
        qty = min(np.floor(risk_cfg["risk_per_trade"] / R), np.floor(risk_cfg["max_notional"] / E),
                  np.floor(risk_cfg["max_vol_participation"] * np.nan_to_num(avgv[ie])))
        busy_until = t[ix] + np.timedelta64(5, "m")
        if qty < 1:
            continue
        ef, xf = E * (1 + d * slip), X * (1 - d * slip)
        buy_v = (ef if d == 1 else xf) * qty
        sell_v = (xf if d == 1 else ef) * qty
        ch = float(intraday_charges(buy_v, sell_v, costs))
        gross_raw = d * (X - E) * qty
        net = d * (xf - ef) * qty - ch
        per_day[day[ie]] = per_day.get(day[ie], 0) + 1
        trades.append({"symbol": sym, "side": "LONG" if d == 1 else "SHORT", "signal_time": s["sig_time"],
                       "entry_time": t[ie], "exit_time": t[ix], "entry": E, "sl": sl, "tp": tp, "exit": X,
                       "reason": why, "qty": qty, "risk_rs": qty * R, "risk_pct": 100 * R / E,
                       "gross_R": gross_raw / (qty * R), "net": net, "R": net / (qty * R), "charges": ch})
    return trades


# ---------------------------------------------------------------- metrics
def stats(t: pd.DataFrame) -> dict:
    if t.empty:
        return {"trades": 0}
    eq = t.sort_values("exit_time")["net"].cumsum()
    dd = float((eq - eq.cummax()).min())
    pos, neg = t.loc[t.net > 0, "net"].sum(), -t.loc[t.net < 0, "net"].sum()
    mon = t.groupby(pd.to_datetime(t["exit_time"]).dt.to_period("M"))["net"].sum()
    return {"trades": len(t), "win_pct": round(100 * (t.net > 0).mean(), 1),
            "avg_R_gross": round(t.gross_R.mean(), 3), "avg_R_net": round(t.R.mean(), 3),
            "cost_R": round((t.gross_R - t.R).mean(), 3), "PF": round(pos / neg, 2) if neg else np.inf,
            "net_rs": round(t.net.sum()), "max_dd_rs": round(dd), "pos_month_pct": round(100 * (mon > 0).mean(), 1),
            "avg_risk_pct": round(t.risk_pct.mean(), 2), "tp_pct": round(100 * (t.reason == "TP").mean(), 1),
            "sl_pct": round(100 * (t.reason == "SL").mean(), 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="research_results/mtf_smc")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--raw-dir", default="")
    a = ap.parse_args()
    cfg = load_config()
    if a.raw_dir:
        cfg["data"]["raw_dir"] = a.raw_dir
    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    log = get_logger("mtf", out / "run.log")
    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()] or load_universe(cfg)
    clean, q = validate_universe(cfg, syms, log)
    clean.pop(cfg["market_symbol"], None)
    q.to_csv(out / "data_quality.csv", index=False)
    log.info("usable symbols: %d", len(clean))

    variants = [(f4, m, rr) for f4 in (True, False) for m in ("MKT", "LIM") for rr in P["rrs"]]
    allt = {v: [] for v in variants}
    nsig = 0
    t0 = time.time()
    for n, (sym, df) in enumerate(sorted(clean.items()), 1):
        df = df.reset_index(drop=True)
        try:
            sigs = find_signals(df, cfg)
        except Exception as e:  # noqa: BLE001
            log.warning("%s signal error: %s", sym, e)
            continue
        nsig += len(sigs)
        for v in variants:
            allt[v].extend(simulate(df, sigs, v[1], v[2], v[0], cfg, sym))
        if n % 20 == 0:
            log.info("%d/%d symbols done (%.0fs)", n, len(clean), time.time() - t0)

    days = sorted({d for df in clean.values() for d in df["datetime"].dt.normalize().unique()})
    c1, c2 = days[int(len(days) * 0.5)], days[int(len(days) * 0.75)]
    rows, keep = [], []
    for (f4, m, rr), tl in allt.items():
        t = pd.DataFrame(tl)
        name = f"{'4H_ON' if f4 else '4H_OFF'}|{m}|RR{rr}"
        if t.empty:
            rows.append({"variant": name, "period": "ALL", "trades": 0})
            continue
        t["variant"] = name
        et = pd.to_datetime(t["entry_time"]).dt.normalize()
        t["period"] = np.where(et < c1, "DEV", np.where(et < c2, "VAL", "OOS"))
        keep.append(t)
        for per, sub in [("ALL", t), ("DEV", t[t.period == "DEV"]), ("VAL", t[t.period == "VAL"]),
                         ("OOS", t[t.period == "OOS"]), ("ALL_LONG", t[t.side == "LONG"]),
                         ("ALL_SHORT", t[t.side == "SHORT"])]:
            rows.append({"variant": name, "period": per, **stats(sub)})
    summ = pd.DataFrame(rows)
    summ.to_csv(out / "summary.csv", index=False)
    if keep:
        tr = pd.concat(keep)
        tr.to_csv(out / "trades_all.csv.gz", index=False, compression="gzip")
        best = summ[summ.period == "ALL"].sort_values("avg_R_net", ascending=False).iloc[0]["variant"]
        tr[tr.variant == best].groupby("symbol").agg(trades=("R", "size"), avg_R=("R", "mean"),
                                                     net=("net", "sum")).sort_values("net").to_csv(out / "best_by_symbol.csv")
    write_json(out / "meta.json", {"params": P, "symbols": len(clean), "signals_total": nsig,
                                   "dev_until": str(c1), "val_until": str(c2), "first_day": str(days[0]),
                                   "last_day": str(days[-1])})
    log.info("done in %.0fs\n%s", time.time() - t0, summ[summ.period.isin(["ALL", "OOS"])].to_string())


if __name__ == "__main__":
    main()
