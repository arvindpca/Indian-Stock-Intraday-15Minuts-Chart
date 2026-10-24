#!/usr/bin/env python3
"""
FINAL INTRADAY STRATEGY BACKTEST - 180 din, Angel One 5-min data, Nifty 500
==========================================================================

Portfolio rules: Rs 50,000 per trade | max 5 trades khule | ek stock me ek hi trade |
signal wale din hi entry | 3:15 square-off | cost 0.1% round trip |
5 se zyada signal ek saath -> jisme volume sabse zyada uchhla wo pehle.

FINAL STRATEGIES (12 Aug - 23 Oct 2026 ke analysis se pehle hi tay; yahan badle nahi):
  1. ORB Short (subah) : 5m, 9:15-9:30 range ka low volume ke saath toote -> Short.
                          Entry sirf 12:00 tak. SL = range ka beech. TP = 1.5R, warna 3:15.
  2. Strong Market Buy : 15m VWAP pullback, stock 1h uptrend + Nifty intraday upar + Nifty 1h uptrend.
                          SL = signal candle ka low. TP nahi, 3:15 exit.

ASLI TEST: "NAYA DATA" = 12 Aug 2026 se pehle ka data, jo rules banate waqt dekha hi nahi gaya.
Strategy tabhi sachchi maani jayegi jab NAYA DATA pe bhi profit de.

Saath me poora grid (4 setups x 5m/15m x filter x Buy/Short x SL x TP x entry cutoff) bhi chalta hai,
taaki "Short me kitna, Buy me kitna, kis time kitna" saaf dikhe.
"""
import itertools
import math
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import intra_core as ic  # noqa: E402

DATA_DIR = HERE.parent / "data" / "nifty500_5m"
OUT_DIR = Path(os.getenv("OUT_DIR", str(HERE / "results" / "latest")))
OUT_DIR.mkdir(parents=True, exist_ok=True)

PER_TRADE, MAX_POS = 50_000, 5
CAPITAL = PER_TRADE * MAX_POS
COST_PCT = 0.1
DESIGN_START = pd.Timestamp("2026-08-12")  # isse pehle = naya (unseen) data

FINAL = [
    dict(name="ORB Short (subah)", strategy="ORB", tf=5, filt="OFF", side="Short", sl="Structure", tp=1.5, cutoff="12:00"),
    dict(name="Strong Market Buy", strategy="VWAP_PULLBACK", tf=15, filt="STRICT", side="Buy", sl="Structure", tp=None, cutoff="14:30"),
]
CUTOFFS = ["11:00", "12:00", "14:30"]

pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 30)
pd.set_option("display.max_rows", 200)

LOG = []


def out(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    LOG.append(s)


# ------------------------------------------------------------------ data
def load_csv(p):
    d = pd.read_csv(p, parse_dates=["time"]).set_index("time").sort_index()
    d = d.rename(columns=str.title)[["Open", "High", "Low", "Close", "Volume"]]
    d = d[~d.index.duplicated()]
    m = d.index.hour * 60 + d.index.minute
    return d[(m >= 555) & (m < 930)]


def nifty_ctx(n):
    tp = (n["High"] + n["Low"] + n["Close"]) / 3
    avg = tp.groupby(n.index.normalize()).expanding().mean().droplevel(0)
    state = np.where(n["Close"] > avg, 1, -1)
    ends = (n.index + pd.Timedelta(minutes=5)).values
    return (ends, state), ic.trend_1h(n)


# ------------------------------------------------------------------ portfolio
def run_portfolio(c):
    if c.empty:
        return pd.DataFrame()
    c = c.sort_values(["entry_time", "vr"], ascending=[True, False])
    open_pos, taken = [], []
    for r in c.itertuples(index=False):
        open_pos = [p for p in open_pos if p[0] > r.entry_time]
        if len(open_pos) >= MAX_POS or any(p[1] == r.symbol for p in open_pos):
            continue
        qty = math.floor(PER_TRADE / r.entry)
        if qty < 1:
            continue
        side = 1 if r.side == "Buy" else -1
        pnl = qty * (r.exit - r.entry) * side - qty * r.entry * COST_PCT / 100
        taken.append(dict(symbol=r.symbol, side=r.side, entry_time=r.entry_time, exit_time=r.exit_time,
                          qty=qty, entry=r.entry, sl=r.sl, exit=r.exit, reason=r.reason, pnl=round(pnl, 2),
                          strategy=getattr(r, "strategy", "")))
        open_pos.append((r.exit_time, r.symbol))
    return pd.DataFrame(taken)


def stats(t):
    if t.empty:
        return dict(trades=0, total_pnl=0)
    t = t.sort_values("exit_time")
    p = t["pnl"]
    eq = p.cumsum()
    w, l_ = p[p > 0], p[p <= 0]
    new = t["entry_time"] < DESIGN_START
    days = t["entry_time"].dt.normalize().nunique()
    return dict(
        trades=len(p), win_rate=round(len(w) / len(p) * 100, 1), total_pnl=round(p.sum()),
        return_pct=round(p.sum() / CAPITAL * 100, 1), avg_pnl=round(p.mean()),
        profit_factor=round(w.sum() / abs(l_.sum()), 2) if l_.sum() else np.inf,
        max_dd=round((eq - eq.cummax()).min()),
        naya_data_pnl=round(p[new].sum()), design_period_pnl=round(p[~new].sum()),
        trade_days=days,
    )


def candidates(prepared, strategy, tf, filt, sl, tp, cutoff, test_start):
    cut = ic.hm(cutoff)
    rows = []
    for s, (A, sig) in prepared[tf].items():
        if sig.empty:
            continue
        ss = sig[sig["strategy"] == strategy]
        if filt == "ON":
            ss = ss[ss["aligned"]]
        elif filt == "STRICT":
            ss = ss[ss["strict"]]
        ss = ss[A["mins"][ss["entry_idx"].to_numpy()] <= cut]
        if not ss.empty:
            rows += ic.simulate(s, A, ss.sort_values("entry_idx"), test_start, sl, tp, 0)
    c = pd.DataFrame(rows, columns=ic.COLS)
    c["strategy"] = strategy
    return c


def detail(t, title):
    out(f"\n---------------- {title} ----------------")
    st = stats(t)
    if not st["trades"]:
        out("Koi trade nahi.")
        return st
    out(f"Trades {st['trades']} | Win {st['win_rate']}% | Kul profit Rs {st['total_pnl']:,} "
        f"({st['return_pct']}% on Rs {CAPITAL:,}) | Avg/trade Rs {st['avg_pnl']} | PF {st['profit_factor']} | "
        f"Max drawdown Rs {st['max_dd']:,}")
    out(f">> NAYA DATA (12 Aug se pehle, rules banane me nahi dekha): Rs {st['naya_data_pnl']:,}")
    out(f">> Design period (12 Aug ke baad):                          Rs {st['design_period_pnl']:,}")
    t = t.copy()
    t["month"] = t["entry_time"].dt.to_period("M")
    t["hour"] = t["entry_time"].dt.strftime("%H:00")
    t["wday"] = t["entry_time"].dt.day_name().str[:3]
    for col, lbl in [("month", "Mahine ke hisaab se"), ("hour", "Entry time ke hisaab se"),
                     ("wday", "Din ke hisaab se"), ("reason", "Exit reason")]:
        g = t.groupby(col)["pnl"].agg(trades="count", pnl="sum", avg="mean",
                                      win_pct=lambda x: (x > 0).mean() * 100).round(0)
        out(f"\n{lbl}:\n{g.to_string()}")
    if "strategy" in t and t["strategy"].nunique() > 1:
        out("\nStrategy ke hisaab se:\n" + t.groupby("strategy")["pnl"].agg(trades="count", pnl="sum").round(0).to_string())
    days = t.groupby(t["entry_time"].dt.normalize())["pnl"].sum()
    out(f"\nDin-wise: {len(days)} trading din | profit wale din {(days > 0).sum()} | loss wale {(days <= 0).sum()} | "
        f"best din Rs {days.max():,.0f} | worst din Rs {days.min():,.0f}")
    return st


def main():
    files = sorted(p for p in DATA_DIR.glob("*_5m.csv") if p.name != "NIFTY50_5m.csv")
    if not files:
        sys.exit("Data nahi mila - pehle download_nifty500_5m.py chalao.")
    nifty = load_csv(DATA_DIR / "NIFTY50_5m.csv")
    nctx, ntr = nifty_ctx(nifty)
    data = {}
    for p in files:
        d = load_csv(p)
        if len(d) > 2000:
            data[p.name.replace("_5m.csv", "")] = d
    all_days = sorted(set().union(*[set(d.index.normalize()) for d in data.values()]))
    test_start = all_days[ic.CFG["warmup_days"]]
    out(f"Stocks: {len(data)} | Data: {all_days[0].date()} se {all_days[-1].date()} | "
        f"Test: {test_start.date()} se ({len(all_days) - ic.CFG['warmup_days']} trading din)")
    out(f"Rs {PER_TRADE:,}/trade | max {MAX_POS} khule | capital Rs {CAPITAL:,} | cost {COST_PCT}%")

    prepared = {tf: {} for tf in ic.TIMEFRAMES}
    for i, (s, d5) in enumerate(data.items(), 1):
        tctx = ic.trend_1h(d5)
        for tf in ic.TIMEFRAMES:
            prepared[tf][s] = ic.prepare(d5, tf, tctx, nctx, ntr)
        if i % 100 == 0:
            out(f"  signals ready {i}/{len(data)}")

    # ---------------- PART 1: FINAL strategies
    out("\n" + "=" * 25 + " PART 1: FINAL STRATEGIES (pehle se tay rules) " + "=" * 25)
    finals = []
    for f in FINAL:
        c = candidates(prepared, f["strategy"], f["tf"], f["filt"], f["sl"], f["tp"], f["cutoff"], test_start)
        c = c[c["side"] == f["side"]]
        c["strategy"] = f["name"]
        finals.append(c)
        t = run_portfolio(c)
        detail(t, f"{f['name']}  (akeli, Rs 2.5 lakh capital)")
        t.to_csv(OUT_DIR / f"final_{f['strategy']}_{f['side']}_trades.csv", index=False)
    comb = run_portfolio(pd.concat(finals))
    detail(comb, "DONO SAATH (ek hi Rs 2.5 lakh, max 5 khule)")
    comb.to_csv(OUT_DIR / "final_combined_trades.csv", index=False)

    # ---------------- PART 2: full grid
    out("\n" + "=" * 25 + " PART 2: POORA GRID (short/buy/time ka analysis) " + "=" * 25)
    rows = []
    combos = list(itertools.product(ic.STRATEGIES, ic.TIMEFRAMES, ic.FILTERS, ic.SL_MODES, ic.TP_MODES))
    for k, (strat, tf, filt, slm, (tpn, tpr)) in enumerate(combos, 1):
        # ek baar 14:30 cutoff pe simulate; chhota cutoff = entry time se filter (baad wala trade
        # pehle wale ko block nahi karta, isliye ye exact hai)
        c_all = candidates(prepared, strat, tf, filt, slm, tpr, "14:30", test_start)
        emins = c_all["entry_time"].dt.hour * 60 + c_all["entry_time"].dt.minute if not c_all.empty else None
        for cut in CUTOFFS:
            c = c_all[emins <= ic.hm(cut)] if not c_all.empty else c_all
            for side in ["Buy", "Short"]:
                t = run_portfolio(c[c["side"] == side])
                rows.append(dict(strategy=strat, tf=f"{tf}m", filter=filt, side=side, SL=slm, TP=tpn,
                                 last_entry=cut, **stats(t)))
        if k % 20 == 0:
            out(f"  grid {k}/{len(combos)}")
    res = pd.DataFrame(rows)
    res.to_csv(OUT_DIR / "grid_results.csv", index=False)
    r = res[res["trades"] >= 30]

    def summ(by):
        return r.groupby(by).agg(combos=("total_pnl", "size"),
                                 profit_combos=("total_pnl", lambda x: int((x > 0).sum())),
                                 naya_data_profit=("naya_data_pnl", lambda x: int((x > 0).sum())),
                                 median_pnl=("total_pnl", "median"), best_pnl=("total_pnl", "max")).round(0)

    out("\nShort vs Buy:\n" + summ("side").to_string())
    out("\nSetup x Side:\n" + summ(["strategy", "side"]).to_string())
    out("\nTimeframe x Side:\n" + summ(["tf", "side"]).to_string())
    out("\nNifty filter x Side:\n" + summ(["filter", "side"]).to_string())
    out("\nLast entry time x Side (time base):\n" + summ(["last_entry", "side"]).to_string())
    out("\nTP x Side:\n" + summ(["TP", "side"]).to_string())
    robust = r[(r["naya_data_pnl"] > 0) & (r["design_period_pnl"] > 0)].sort_values("total_pnl", ascending=False)
    out(f"\nDono hisson (naya data + design period) me profit wale combos: {len(robust)} / {len(r)}")
    out(robust.head(20).to_string(index=False) if not robust.empty else "Koi nahi.")
    out("\nTop 15 (sirf total profit se - inpe akele bharosa mat karo):")
    out(r.sort_values("total_pnl", ascending=False).head(15).to_string(index=False))

    (OUT_DIR / "REPORT.txt").write_text("\n".join(LOG), encoding="utf-8")
    out(f"\nFiles: {OUT_DIR}")


if __name__ == "__main__":
    main()
