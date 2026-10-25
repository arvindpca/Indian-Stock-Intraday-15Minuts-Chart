#!/usr/bin/env python3
"""
STRATEGY FINDER - Round 2: event (trigger) based.

Instead of every candle, look only at specific moments ("events"), first time per stock
per day, between 09:30 and 14:30, in BOTH directions (follow the move / fade the move):
  ORB break, previous-day high/low break, VWAP cross, volume spike candle,
  new day high/low after 11:00, gap-fade, RSI extreme, VWAP pullback in trend.
Outcomes for several SL/TP pairs (1:1 and 1:2), exit 15:15, cost 0.10%.
Rules are chosen on TRAIN (Apr-Aug) only; chosen rules are then reported on TEST (Sep-Oct).
"""
import glob
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.tree import DecisionTreeRegressor

import finder as f1

ROOT = f1.ROOT
OUT = ROOT / "data" / "finder2"
OUT.mkdir(parents=True, exist_ok=True)
SPLIT, COST, EOD = f1.SPLIT, f1.COST, f1.EOD
PAIRS = [(1.0, 1.0), (0.5, 1.0), (0.75, 1.5), (1.0, 2.0)]        # (SL %, TP %)
KEY = "1.0_2.0"                                                  # main score; robustness uses all pairs
# Trailing SL exits, no fixed target: (initial SL %, activate after +%, trail distance %)
# 5th value = fixed target % (None = no target, let it run)
TRAILS = {"tr1": (0.75, 0.75, 0.75, None), "tr2": (1.0, 1.0, 1.0, None), "tr3": (1.0, 1.0, 0.5, None),
          "tr4": (1.5, 1.5, 1.0, None),
          "tr5": (1.0, 1.5, 1.0, 3.0),      # bada target 3% + trail 1% after +1.5%
          "tr6": (1.0, 2.0, 1.0, 4.0)}      # bada target 4% + trail 1% after +2%
TKEY = "tr2"
FAMILIES = {"FIXED": ([f"{sl}_{tp}" for sl, tp in PAIRS], KEY), "TRAIL": (list(TRAILS), TKEY)}
MIN_TRADES = 150


def outcomes(d):
    n = len(d)
    res = {f"{s}_{sl}_{tp}": np.full(n, np.nan) for s in ("long", "short") for sl, tp in PAIRS}
    H, L, C = (d[c].to_numpy(float) for c in ("high", "low", "close"))
    M = d["minute"].to_numpy()
    for _, idx in d.groupby("date").indices.items():
        idx = np.sort(idx)
        e_pos = np.where(M[idx] <= EOD)[0]
        if len(e_pos) == 0:
            continue
        e = e_pos[-1]
        h, l, c = H[idx], L[idx], C[idx]
        k = len(idx)
        valid = (np.arange(k)[None, :] > np.arange(k)[:, None]) & (np.arange(k)[None, :] <= e)
        for sl, tp in PAIRS:
            for side in ("long", "short"):
                if side == "long":
                    tp_lv, sl_lv = c * (1 + tp / 100), c * (1 - sl / 100)
                    win = valid & (h[None, :] >= tp_lv[:, None])
                    loss = valid & (l[None, :] <= sl_lv[:, None])
                    eod = (c[e] / c - 1) * 100
                else:
                    tp_lv, sl_lv = c * (1 - tp / 100), c * (1 + sl / 100)
                    win = valid & (l[None, :] <= tp_lv[:, None])
                    loss = valid & (h[None, :] >= sl_lv[:, None])
                    eod = (1 - c[e] / c) * 100
                fw = np.where(win.any(1), win.argmax(1), 10 ** 6)
                fl = np.where(loss.any(1), loss.argmax(1), 10 ** 6)
                r = np.where(fl <= fw, -sl, tp)
                r = np.where((fw == 10 ** 6) & (fl == 10 ** 6), np.clip(eod, -sl, tp), r)
                res[f"{side}_{sl}_{tp}"][idx] = np.where(np.arange(k) < e, r - COST, np.nan)
    return pd.DataFrame(res, index=d.index)


def trailing(d, rows):
    """Trailing-stop outcome (% net) for entries at `rows` (index positions), both sides.
    Per candle: stop from earlier candles is checked first (gap fills at open), then the
    best price so far is updated with this candle's high/low. Exit 15:15 close otherwise.
    Same rule as backtest_sl_tp.py."""
    H, L, O, C = (d[c].to_numpy(float) for c in ("high", "low", "open", "close"))
    M, D = d["minute"].to_numpy(), d["date"].to_numpy()
    out = {f"{s}_{k}": np.full(len(d), np.nan) for s in ("long", "short") for k in TRAILS}
    for i in rows:
        j = i + 1
        while j < len(d) and D[j] == D[i] and M[j - 1] < EOD:
            j += 1
        if j <= i + 1 or M[i] >= EOD:
            continue
        sl_idx = slice(i + 1, j)
        e = C[i]
        for side in ("long", "short"):
            if side == "long":
                hi, lo, op = (H[sl_idx] / e - 1) * 100, (L[sl_idx] / e - 1) * 100, (O[sl_idx] / e - 1) * 100
                last = (C[j - 1] / e - 1) * 100
            else:
                hi, lo, op = (1 - L[sl_idx] / e) * 100, (1 - H[sl_idx] / e) * 100, (1 - O[sl_idx] / e) * 100
                last = (1 - C[j - 1] / e) * 100
            best = np.maximum.accumulate(np.maximum(hi, 0))
            bprev = np.concatenate([[0.0], best[:-1]])
            for k, (sl, act, dist, tgt) in TRAILS.items():
                stop = np.where(bprev >= act, np.maximum(-sl, bprev - dist), -sl)
                hit = np.where(lo <= stop)[0]
                first_sl = hit[0] if len(hit) else 10 ** 6
                first_tp = 10 ** 6
                if tgt is not None:
                    th = np.where(hi >= tgt)[0]
                    first_tp = th[0] if len(th) else 10 ** 6
                if first_sl <= first_tp and first_sl < 10 ** 6:      # same candle -> stop first
                    r = min(stop[first_sl], op[first_sl])
                elif first_tp < 10 ** 6:
                    r = max(tgt, op[first_tp])
                else:
                    r = last
                out[f"{side}_{k}"][i] = r - COST
    return pd.DataFrame(out, index=d.index)


def events(d, f):
    """boolean columns; True only on the FIRST candle of the day where the event happens"""
    c, o = d["close"], d["open"]
    prev_c = c.shift(1)
    same_day = d["date"].eq(d["date"].shift(1))
    tp = (d["high"] + d["low"] + c) / 3
    vwap = (tp * d["volume"]).groupby(d["date"]).cumsum() / d["volume"].groupby(d["date"]).cumsum().replace(0, np.nan)
    prev_vwap = vwap.shift(1)
    day_hi_before = d.groupby("date")["high"].transform(lambda s: s.cummax().shift(1))
    day_lo_before = d.groupby("date")["low"].transform(lambda s: s.cummin().shift(1))
    ev = {
        "ORB_UP": (c > d["orb_high"]) & ~(prev_c > d["orb_high"]),
        "ORB_DN": (c < d["orb_low"]) & ~(prev_c < d["orb_low"]),
        "PDH_BREAK": (c > d["prev_high"]) & ~(prev_c > d["prev_high"]),
        "PDL_BREAK": (c < d["prev_low"]) & ~(prev_c < d["prev_low"]),
        "VWAP_X_UP": (c > vwap) & (prev_c <= prev_vwap) & same_day,
        "VWAP_X_DN": (c < vwap) & (prev_c >= prev_vwap) & same_day,
        "VOL_SPIKE_GREEN": (f["vol_ratio"] >= 3) & (c > o),
        "VOL_SPIKE_RED": (f["vol_ratio"] >= 3) & (c < o),
        "NEW_HIGH_AFTER_11": (d["minute"] >= 660) & (d["high"] > day_hi_before) & (c > day_hi_before),
        "NEW_LOW_AFTER_11": (d["minute"] >= 660) & (d["low"] < day_lo_before) & (c < day_lo_before),
        "GAPUP_ORB_FAIL": (f["gap_pct"] >= 1) & (c < d["orb_low"]),
        "GAPDN_ORB_HOLD": (f["gap_pct"] <= -1) & (c > d["orb_high"]),
        "RSI_LOW": f["rsi"] < 25,
        "RSI_HIGH": f["rsi"] > 75,
        "PULLBACK_UPTREND": (f["day_ret"] > 1) & (d["low"] <= vwap) & (c > vwap),
        "PULLBACK_DNTREND": (f["day_ret"] < -1) & (d["high"] >= vwap) & (c < vwap),
    }
    out = {}
    for k, v in ev.items():
        v = v.fillna(False).astype(bool) & (d["minute"] >= f1.ENTRY_FROM) & (d["minute"] <= f1.ENTRY_TO)
        first = v & (v.groupby(d["date"]).cumsum() == 1)
        out[k] = first
    return out


def build():
    nifty = f1.load_nifty()
    rows = []
    files = sorted(glob.glob(str(ROOT / "data" / "angel_5m" / "*_5m.csv")))
    for k, path in enumerate(files, 1):
        d = f1.load_stock(path)
        if len(d) < 300:
            continue
        f = f1.features(d, nifty)
        y = outcomes(d)
        ev = events(d, f)
        any_ev = np.zeros(len(d), bool)
        for v in ev.values():
            any_ev |= v.to_numpy()
        tr_out = trailing(d, np.where(any_ev)[0])
        base = pd.concat([f, y, tr_out], axis=1)
        base["symbol"] = Path(path).name.replace("_5m.csv", "")
        base["time"] = d["time"].dt.strftime("%Y-%m-%d %H:%M")
        base["date"] = d["date"].astype(str)
        for name, mask in ev.items():
            x = base[mask.to_numpy()].copy()
            x["event"] = name
            rows.append(x)
        if k % 25 == 0:
            print(f"  built {k}/{len(files)}", flush=True)
    t = pd.concat(rows, ignore_index=True)
    t = t[t["turnover_lakh"] >= f1.LIQ_MIN_LAKH].dropna(subset=[f"long_{KEY}"])
    t["period"] = np.where(t["date"] < SPLIT, "TRAIN", "TEST")
    return t


ALL_EXITS = [f"{sl}_{tp}" for sl, tp in PAIRS] + list(TRAILS)


def stats(x, side):
    out = {"trades": len(x)}
    if len(x) == 0:
        return out
    for k in ALL_EXITS:
        v = x[f"{side}_{k}"]
        out[f"avg_{k}"] = round(v.mean(), 3)
        out[f"win_{k}"] = round((v > 0).mean() * 100, 1)
    out["rs_" + KEY] = round(x[f"{side}_{KEY}"].sum() / 100 * 1e5, 0)
    out["rs_" + TKEY] = round(x[f"{side}_{TKEY}"].sum() / 100 * 1e5, 0)
    out["days"] = x["date"].nunique()
    return out


def robust(st, family):
    keys = FAMILIES[family][0]
    return st["trades"] >= MIN_TRADES and all(st[f"avg_{k}"] > 0 for k in keys)


def main():
    t = build()
    tr, te = t[t.period == "TRAIN"], t[t.period == "TEST"]
    print(f"event rows train={len(tr)} test={len(te)}", flush=True)
    FEATS = f1.FEATS
    summary = {"events_train": len(tr), "events_test": len(te)}

    # A) every event x direction, no filter (TRAIN only), all exits incl. trailing
    base_rows = []
    for ev, g in tr.groupby("event"):
        for side in ("long", "short"):
            base_rows.append({"event": ev, "side": side, **stats(g, side)})
    pd.DataFrame(base_rows).sort_values(f"avg_{TKEY}", ascending=False).to_csv(OUT / "events_train.csv", index=False)

    for family, (keys, score) in FAMILIES.items():
        # B) per event x direction: shallow tree on TRAIN (target = family score) -> rules
        cands = []
        for ev, g in tr.groupby("event"):
            for side in ("long", "short"):
                st = stats(g, side)
                cands.append({"event": ev, "side": side, "rule": "(no filter)", "conds": [],
                              **{f"TRAIN_{k}": v for k, v in st.items()}, "robust": robust(st, family)})
                if len(g) < 3 * MIN_TRADES:
                    continue
                y = g[f"{side}_{score}"]
                ok = y.notna()
                m = DecisionTreeRegressor(max_depth=2, min_samples_leaf=MIN_TRADES, random_state=0)
                m.fit(g.loc[ok, FEATS].fillna(-999), y[ok])
                tt = m.tree_

                def walk(node, conds):
                    if tt.children_left[node] == -1:
                        if conds:
                            s2 = stats(g[f1.mask_for(g, conds)], side)
                            cands.append({"event": ev, "side": side, "rule": f1.describe(conds), "conds": conds,
                                          **{f"TRAIN_{k}": v for k, v in s2.items()}, "robust": robust(s2, family)})
                        return
                    fname, thr = FEATS[tt.feature[node]], float(tt.threshold[node])
                    walk(tt.children_left[node], conds + [(fname, "<=", thr)])
                    walk(tt.children_right[node], conds + [(fname, ">", thr)])
                walk(0, [])
        c = pd.DataFrame(cands).sort_values(["robust", f"TRAIN_avg_{score}"], ascending=False)
        c.drop(columns=["conds"]).to_csv(OUT / f"all_candidates_train_{family}.csv", index=False)
        summary[family] = {"candidates_tested": len(c), "robust_on_train": int(c["robust"].sum())}

        # C) chosen = robust on TRAIN, best 8 by TRAIN score; only now look at TEST
        chosen = c[c["robust"]].head(8)
        rows, monthly = [], []
        for _, r in chosen.iterrows():
            gte = te[te.event == r["event"]]
            gte = gte[f1.mask_for(gte, r["conds"])] if r["conds"] else gte
            rows.append({f"TEST_{k}": v for k, v in stats(gte, r["side"]).items()})
            gall = t[t.event == r["event"]]
            gall = gall[f1.mask_for(gall, r["conds"])] if r["conds"] else gall
            gall = gall.assign(month=gall["date"].str[:7])
            for mon, x in gall.groupby("month"):
                monthly.append({"event": r["event"], "side": r["side"], "rule": r["rule"], "month": mon,
                                "trades": len(x), "rs": round(x[f"{r['side']}_{score}"].sum() / 100 * 1e5, 0)})
        if len(chosen):
            out = pd.concat([chosen.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
            out.drop(columns=["conds"]).to_csv(OUT / f"chosen_rules_{family}.csv", index=False)
            pd.DataFrame(monthly).to_csv(OUT / f"chosen_monthly_{family}.csv", index=False)
            with open(OUT / f"chosen_rules_{family}.json", "w") as fh:
                json.dump([{"event": r["event"], "side": r["side"], "rule": r["rule"], "conds": r["conds"]}
                           for _, r in chosen.iterrows()], fh, indent=1)
    keep = ["symbol", "date", "time", "event", "period"] + FEATS + [f"{s}_{k}" for s in ("long", "short") for k in ALL_EXITS]
    t[keep].to_csv(OUT / "events.csv.gz", index=False, compression="gzip")
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
