#!/usr/bin/env python3
"""
STRATEGY FINDER - find what was true just BEFORE profitable intraday moves.

Step 1  every 5m candle 09:30-14:30 = possible entry (at that candle's close)
Step 2  outcome for LONG and SHORT, NO fixed SL/TP yet: symmetric barrier test at
        0.75%, 1%, 1.5% (who is hit first: +x or -x, else exit 15:15 close), cost 0.10%
Step 3  features known AT the entry candle (time, gap, ORB, prev-day H/L, VWAP, volume,
        RSI, ATR, trend, Nifty, relative strength ...)
Step 4  compare good vs bad moments on TRAIN (Apr-Aug) only: one-feature tables +
        a shallow decision tree -> candidate rules (max 3 conditions)
Step 5  candidate rules turned into trades (first signal per stock per day) and judged
        on TRAIN; only the rules chosen on TRAIN are then shown on TEST (Sep-Oct).
SL/TP is tuned later, only for the finalists.
"""
import glob
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.tree import DecisionTreeRegressor

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "finder"
OUT.mkdir(parents=True, exist_ok=True)
IST = "Asia/Kolkata"
SPLIT = "2026-09-01"
COST = 0.10
XS = (0.75, 1.0, 1.5)                       # barrier sizes in %
ENTRY_FROM, ENTRY_TO, EOD = 9 * 60 + 30, 14 * 60 + 30, 15 * 60 + 15
MIN_TRADES = int(os.getenv("FINDER_MIN_TRADES", "150"))   # min trades a rule needs in TRAIN
LIQ_MIN_LAKH = 5.0                           # median 5m traded value must be >= Rs 5 lakh


# ------------------------------------------------------------------ data
def load_stock(path):
    df = pd.read_csv(path)
    tcol = "time_ist" if "time_ist" in df.columns else "time_utc"
    df["time"] = pd.to_datetime(df[tcol], utc=True).dt.tz_convert(IST)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    m = df["time"].dt.hour * 60 + df["time"].dt.minute
    df = df[(m >= 555) & (m <= 925)].sort_values("time").drop_duplicates("time").reset_index(drop=True)
    df["volume"] = df["volume"].fillna(0.0)
    df["minute"] = df["time"].dt.hour * 60 + df["time"].dt.minute
    df["date"] = df["time"].dt.date
    return df


def load_nifty():
    n = pd.read_csv(ROOT / "data" / "nifty" / "NIFTY_5m.csv")
    n["time"] = pd.to_datetime(n["time_ist"], utc=True).dt.tz_convert(IST)
    n = n.sort_values("time").drop_duplicates("time").reset_index(drop=True)
    n["date"] = n["time"].dt.date
    n["n_day_ret"] = (n["close"] / n.groupby("date")["open"].transform("first") - 1) * 100
    n["n_vs_ema20"] = (n["close"] / n["close"].ewm(span=20, adjust=False).mean() - 1) * 100
    n["n_ret_15m"] = (n["close"] / n["close"].shift(3) - 1) * 100
    d = pd.read_csv(ROOT / "data" / "nifty" / "NIFTY_1d.csv")
    d["date"] = pd.to_datetime(d["time_ist"], utc=True).dt.tz_convert(IST).dt.date
    d = d.sort_values("date").drop_duplicates("date").set_index("date")
    up = (d["close"] > d["close"].ewm(span=20, adjust=False).mean()).astype(float).shift(1)
    n["n_daily_up"] = n["date"].map(up)
    return n.set_index("time")[["n_day_ret", "n_vs_ema20", "n_ret_15m", "n_daily_up"]]


# ------------------------------------------------------------------ features
def features(df, nifty):
    d = df
    pc = d["close"].shift(1)
    tr = pd.concat([d["high"] - d["low"], (d["high"] - pc).abs(), (d["low"] - pc).abs()], axis=1).max(axis=1)
    d["atr"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    g = d.groupby("date")
    day_open = g["open"].transform("first")
    d["day_high"] = g["high"].cummax()
    d["day_low"] = g["low"].cummin()
    daily = g.agg(o=("open", "first"), h=("high", "max"), l=("low", "min"), c=("close", "last"))
    prev = daily.shift(1)
    d["prev_close"] = d["date"].map(prev["c"])
    d["prev_high"] = d["date"].map(prev["h"])
    d["prev_low"] = d["date"].map(prev["l"])
    prev_ret = (daily["c"] / daily["c"].shift(1) - 1) * 100
    d["prev_day_ret"] = d["date"].map(prev_ret.shift(1))
    d["prev_range_pct"] = (d["prev_high"] / d["prev_low"] - 1) * 100
    # opening range = first 3 candles (09:15-09:30)
    first3 = d[d["minute"] < 570].groupby("date").agg(oh=("high", "max"), ol=("low", "min"))
    d["orb_high"], d["orb_low"] = d["date"].map(first3["oh"]), d["date"].map(first3["ol"])
    tp = (d["high"] + d["low"] + d["close"]) / 3
    vwap = (tp * d["volume"]).groupby(d["date"]).cumsum() / d["volume"].groupby(d["date"]).cumsum().replace(0, np.nan)
    delta = d["close"].diff()
    ag = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    al = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()

    f = pd.DataFrame(index=d.index)
    f["minute"] = d["minute"]
    f["weekday"] = d["time"].dt.weekday
    f["gap_pct"] = (day_open / d["prev_close"] - 1) * 100
    f["day_ret"] = (d["close"] / day_open - 1) * 100
    f["prev_day_ret"] = d["prev_day_ret"]
    f["prev_range_pct"] = d["prev_range_pct"]
    rng = (d["orb_high"] - d["orb_low"]).replace(0, np.nan)
    f["orb_pos"] = (d["close"] - d["orb_low"]) / rng              # >1 above ORB, <0 below
    f["orb_width_pct"] = rng / d["close"] * 100
    f["vs_prev_high"] = (d["close"] / d["prev_high"] - 1) * 100
    f["vs_prev_low"] = (d["close"] / d["prev_low"] - 1) * 100
    f["vwap_dist"] = (d["close"] / vwap - 1) * 100
    f["vol_ratio"] = d["volume"] / d["volume"].rolling(20).mean()
    f["rsi"] = 100 - 100 / (1 + ag / al.replace(0, np.nan))
    f["body_atr"] = (d["close"] - d["open"]) / d["atr"]
    f["atr_pct"] = d["atr"] / d["close"] * 100
    f["ret_15m"] = (d["close"] / d["close"].shift(3) - 1) * 100
    f["ret_1h"] = (d["close"] / d["close"].shift(12) - 1) * 100
    f["ema20_dist"] = (d["close"] / d["close"].ewm(span=20, adjust=False).mean() - 1) * 100
    f["ema_trend"] = (d["close"].ewm(span=20, adjust=False).mean() / d["close"].ewm(span=50, adjust=False).mean() - 1) * 100
    span = (d["day_high"] - d["day_low"]).replace(0, np.nan)
    f["day_range_pos"] = (d["close"] - d["day_low"]) / span
    f["day_range_pct"] = span / day_open * 100
    nf = nifty.reindex(d["time"]).reset_index(drop=True)
    for c in nf.columns:
        f[c] = nf[c].to_numpy()
    f["rel_strength"] = f["day_ret"] - f["n_day_ret"]
    f["turnover_lakh"] = (d["close"] * d["volume"] / 1e5).rolling(20).median()
    return f


# ------------------------------------------------------------------ outcomes (no fixed SL/TP)
def outcomes(d):
    n = len(d)
    res = {f"{s}_{x}": np.full(n, np.nan) for s in ("long", "short") for x in XS}
    res["eod_long"] = np.full(n, np.nan)
    H, L, C = (d[c].to_numpy(float) for c in ("high", "low", "close"))
    M = d["minute"].to_numpy()
    for _, idx in d.groupby("date").indices.items():
        idx = np.sort(idx)
        m = M[idx]
        eod_pos = np.where(m <= EOD)[0]
        if len(eod_pos) == 0:
            continue
        e = eod_pos[-1]
        h, l, c = H[idx], L[idx], C[idx]
        k = len(idx)
        ii = np.arange(k)[:, None]
        jj = np.arange(k)[None, :]
        valid = (jj > ii) & (jj <= e)
        for x in XS:
            up, dn = c * (1 + x / 100), c * (1 - x / 100)
            for side in ("long", "short"):
                if side == "long":
                    win = valid & (h[None, :] >= up[:, None])
                    loss = valid & (l[None, :] <= dn[:, None])
                else:
                    win = valid & (l[None, :] <= dn[:, None])
                    loss = valid & (h[None, :] >= up[:, None])
                fw = np.where(win.any(1), win.argmax(1), 10 ** 6)
                fl = np.where(loss.any(1), loss.argmax(1), 10 ** 6)
                eod_r = (c[e] / c - 1) * 100 * (1 if side == "long" else -1)
                r = np.where(fl <= fw, np.where(fl < 10 ** 6, -x, 0), x)     # same candle -> loss
                r = np.where((fw == 10 ** 6) & (fl == 10 ** 6), np.clip(eod_r, -x, x), r)
                r = np.where(np.arange(k) < e, r - COST, np.nan)
                res[f"{side}_{x}"][idx] = r
        res["eod_long"][idx] = np.where(np.arange(k) < e, (c[e] / c - 1) * 100, np.nan)
    return pd.DataFrame(res, index=d.index)


# ------------------------------------------------------------------ build table
def build():
    nifty = load_nifty()
    parts = []
    files = sorted(glob.glob(str(ROOT / "data" / "angel_5m" / "*_5m.csv")))
    for k, f in enumerate(files, 1):
        d = load_stock(f)
        if len(d) < 300:
            continue
        x = features(d, nifty)
        y = outcomes(d)
        t = pd.concat([x, y], axis=1)
        t["symbol"] = Path(f).name.replace("_5m.csv", "")
        t["time"] = d["time"].dt.strftime("%Y-%m-%d %H:%M")
        t["date"] = d["date"].astype(str)
        parts.append(t[(t["minute"] >= ENTRY_FROM) & (t["minute"] <= ENTRY_TO)])
        if k % 25 == 0:
            print(f"  built {k}/{len(files)}", flush=True)
    t = pd.concat(parts, ignore_index=True)
    t = t[t["turnover_lakh"] >= LIQ_MIN_LAKH].dropna(subset=["long_1.0", "gap_pct", "rsi", "n_day_ret"])
    t["period"] = np.where(t["date"] < SPLIT, "TRAIN", "TEST")
    return t


FEATS = ["minute", "weekday", "gap_pct", "day_ret", "prev_day_ret", "prev_range_pct", "orb_pos",
         "orb_width_pct", "vs_prev_high", "vs_prev_low", "vwap_dist", "vol_ratio", "rsi", "body_atr",
         "atr_pct", "ret_15m", "ret_1h", "ema20_dist", "ema_trend", "day_range_pos", "day_range_pct",
         "n_day_ret", "n_vs_ema20", "n_ret_15m", "n_daily_up", "rel_strength"]


# ------------------------------------------------------------------ analysis helpers
def univariate(tr):
    rows = []
    for side in ("long", "short"):
        base = tr[f"{side}_1.0"].mean()
        for f in FEATS:
            s = tr[f]
            if s.nunique() <= 6:
                bins = s
            else:
                bins = pd.qcut(s, 5, duplicates="drop")
            g = tr.groupby(bins, observed=True)
            for b, x in g:
                rows.append({"side": side, "feature": f, "bin": str(b), "candles": len(x),
                             "avg_pnl_1x1": round(x[f"{side}_1.0"].mean(), 3),
                             "uplift": round(x[f"{side}_1.0"].mean() - base, 3),
                             "win_1x1_pct": round((x[f"{side}_1.0"] > 0).mean() * 100, 1),
                             "avg_pnl_0.75": round(x[f"{side}_0.75"].mean(), 3),
                             "avg_pnl_1.5": round(x[f"{side}_1.5"].mean(), 3)})
    u = pd.DataFrame(rows)
    u["spread"] = u.groupby(["side", "feature"])["avg_pnl_1x1"].transform(lambda v: v.max() - v.min())
    return u


def tree_rules(tr, side, depth=3, leaf_frac=0.01):
    X, y = tr[FEATS].fillna(-999), tr[f"{side}_1.0"]
    m = DecisionTreeRegressor(max_depth=depth, min_samples_leaf=max(2000, int(len(tr) * leaf_frac)), random_state=0)
    m.fit(X, y)
    t = m.tree_
    rules = []

    def walk(node, conds):
        if t.children_left[node] == -1:
            rules.append({"conds": conds, "candles": int(t.n_node_samples[node]), "avg": float(t.value[node][0][0])})
            return
        f, thr = FEATS[t.feature[node]], float(t.threshold[node])
        walk(t.children_left[node], conds + [(f, "<=", thr)])
        walk(t.children_right[node], conds + [(f, ">", thr)])
    walk(0, [])
    return rules


def mask_for(df, conds):
    m = np.ones(len(df), bool)
    for f, op, thr in conds:
        v = df[f].fillna(-999).to_numpy()
        m &= (v <= thr) if op == "<=" else (v > thr)
    return m


def trades_for(df, conds, side):
    """first qualifying candle per stock per day = one trade"""
    x = df[mask_for(df, conds)].sort_values("time").drop_duplicates(["symbol", "date"])
    return x


def trade_stats(x, side):
    if len(x) == 0:
        return {"trades": 0}
    out = {"trades": len(x), "days": x["date"].nunique()}
    for b in XS:
        v = x[f"{side}_{b}"]
        out[f"avg_{b}"] = round(v.mean(), 3)
        out[f"win_{b}"] = round((v > 0).mean() * 100, 1)
        out[f"rs_{b}"] = round(v.sum() / 100 * 100000, 0)
    return out


def describe(conds):
    names = {"minute": "time(min)", "n_daily_up": "nifty_daily_up"}
    parts = []
    for f, op, thr in conds:
        if f == "minute":
            hh, mm = divmod(int(thr), 60)
            parts.append(f"time {op} {hh:02d}:{mm:02d}")
        else:
            parts.append(f"{names.get(f, f)} {op} {thr:.3g}")
    return " AND ".join(parts)


# ------------------------------------------------------------------ main
def main():
    t = build()
    tr, te = t[t["period"] == "TRAIN"], t[t["period"] == "TEST"]
    print(f"rows train={len(tr)} test={len(te)} symbols={t['symbol'].nunique()}", flush=True)

    summary = {"rows_train": len(tr), "rows_test": len(te), "symbols": int(t["symbol"].nunique()),
               "liq_min_lakh": LIQ_MIN_LAKH, "turnover_lakh_quantiles":
               {str(q): round(float(t["turnover_lakh"].quantile(q)), 1) for q in (0.1, 0.25, 0.5, 0.75)}}
    for side in ("long", "short"):
        summary[f"baseline_{side}"] = {f"avg_{x}": round(tr[f"{side}_{x}"].mean(), 3) for x in XS}
        summary[f"baseline_{side}"]["win_1x1"] = round((tr[f"{side}_1.0"] > 0).mean() * 100, 1)

    u = univariate(tr)
    u.to_csv(OUT / "univariate_train.csv", index=False)

    cands = []
    for side in ("long", "short"):
        seen = set()
        for depth, frac in ((2, 0.02), (3, 0.01), (3, 0.005)):
            for r in tree_rules(tr, side, depth, frac):
                key = tuple(r["conds"])
                if key in seen or r["avg"] <= 0:
                    continue
                seen.add(key)
                x_tr = trades_for(tr, r["conds"], side)
                st = trade_stats(x_tr, side)
                if st["trades"] < MIN_TRADES:
                    continue
                cands.append({"side": side, "rule": describe(r["conds"]), "conds": r["conds"],
                              "candle_avg_train": round(r["avg"], 3), **{f"TRAIN_{k}": v for k, v in st.items()}})
    c = pd.DataFrame(cands)
    if len(c):
        # choose ONLY with train numbers: positive at all three barrier sizes, best avg_1.0
        c["robust_train"] = (c["TRAIN_avg_0.75"] > 0) & (c["TRAIN_avg_1.0"] > 0) & (c["TRAIN_avg_1.5"] > 0)
        c = c.sort_values(["robust_train", "TRAIN_avg_1.0"], ascending=False)
        chosen = c[c["robust_train"]].groupby("side").head(5)
        # only now look at TEST for the chosen rules
        test_rows, monthly = [], []
        for _, r in chosen.iterrows():
            x_te = trades_for(te, r["conds"], r["side"])
            test_rows.append({f"TEST_{k}": v for k, v in trade_stats(x_te, r["side"]).items()})
            x_all = trades_for(t, r["conds"], r["side"])
            x_all["month"] = x_all["date"].str[:7]
            mm = x_all.groupby("month")[f"{r['side']}_1.0"].agg(["size", "sum"])
            for mon, v in mm.iterrows():
                monthly.append({"side": r["side"], "rule": r["rule"], "month": mon, "trades": int(v["size"]),
                                "rs_1x1": round(v["sum"] / 100 * 100000, 0)})
        chosen = pd.concat([chosen.reset_index(drop=True), pd.DataFrame(test_rows)], axis=1)
        chosen.drop(columns=["conds"]).to_csv(OUT / "chosen_rules.csv", index=False)
        pd.DataFrame(monthly).to_csv(OUT / "chosen_monthly.csv", index=False)
        with open(OUT / "chosen_rules.json", "w") as fh:
            json.dump([{"side": r["side"], "rule": r["rule"], "conds": r["conds"]} for _, r in chosen.iterrows()], fh, indent=1)
        c.drop(columns=["conds"]).to_csv(OUT / "all_candidates_train.csv", index=False)
        # Per-stock idea: do stocks that did well on a rule in TRAIN also do well in TEST?
        pers = []
        for _, r in chosen.iterrows():
            col = f"{r['side']}_1.0"
            a = trades_for(tr, r["conds"], r["side"]).groupby("symbol")[col].agg(["size", "sum"])
            b = trades_for(te, r["conds"], r["side"]).groupby("symbol")[col].agg(["size", "sum"])
            j = a.join(b, lsuffix="_tr", rsuffix="_te", how="inner")
            j = j[(j["size_tr"] >= 3) & (j["size_te"] >= 1)]
            if len(j) < 10:
                continue
            good = j["sum_tr"] > 0
            pers.append({"side": r["side"], "rule": r["rule"], "stocks": len(j),
                         "corr_train_test": round(j["sum_tr"].corr(j["sum_te"], method="spearman"), 3),
                         "test_rs_train_good_stocks": round(j.loc[good, "sum_te"].sum() / 100 * 1e5, 0),
                         "test_rs_train_bad_stocks": round(j.loc[~good, "sum_te"].sum() / 100 * 1e5, 0),
                         "test_rs_all_stocks": round(j["sum_te"].sum() / 100 * 1e5, 0),
                         "avg_trades_per_stock_train": round(j["size_tr"].mean(), 1)})
        pd.DataFrame(pers).to_csv(OUT / "per_stock_persistence.csv", index=False)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
