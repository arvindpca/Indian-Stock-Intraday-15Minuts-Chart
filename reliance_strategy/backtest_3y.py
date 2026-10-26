"""3 saal ka backtest (Rs 1 lakh, ek position) -- final + purane versions, saal-dar-saal.
Saal 1-2 (Oct 2023 - Oct 2025) strategy banate waqt KABHI nahi dekhe gaye = asli out-of-sample test.
python reliance_strategy/backtest_3y.py
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import book  # noqa: E402

YEARS = [("Saal 1: Oct23-Oct24 (unseen)", "2023-10-01", "2024-10-10"),
         ("Saal 2: Oct24-Oct25 (unseen)", "2024-10-10", "2025-10-10"),
         ("Saal 3: Oct25-Oct26 (jis pe banaya)", "2025-10-10", "2026-11-01")]


def variants(d):
    c = d.close
    raw = d.mom_raw
    pb_base = (c < d.vwap) & ((d.nret3 > book.PB_NIFTY_RET) | (d.adx > book.PB_ADX))
    rsi_b, rsi_s = d.rsi14 <= book.RSI_BUY_MAX, d.rsi14 >= book.RSI_SELL_MIN
    ema_b, ema_s = c > d.ema20, c < d.ema20
    atr_b = d.atrp >= book.MIN_ATRP_BUY
    vw_s = ~((d.vwd > -book.SELL_VWAP_GAP) & (d.vwd < 0))
    sig = lambda b, s: np.where((raw == 1) & b, 1, np.where((raw == -1) & s, -1, 0))
    T = pd.Series(True, index=d.index)
    return {
        "V1 sirf Momentum (koi filter nahi)": (sig(T, T), pd.Series(False, index=d.index)),
        "V2 Momentum + Pullback": (sig(T, T), pb_base),
        "V3 + RSI filter": (sig(rsi_b, rsi_s), pb_base),
        "V4 + EMA20 trend": (sig(rsi_b & ema_b, rsi_s & ema_s), pb_base & ema_s),
        "V5 FINAL (+ATR%, VWAP gap)": (sig(rsi_b & ema_b & atr_b, rsi_s & ema_s & vw_s), pb_base & ema_s),
    }


def main():
    rd = lambda s: pd.read_csv(os.path.join(HERE, "data", f"{s}_5m_3y.csv"), parse_dates=["datetime"]).set_index("datetime")
    d = book.prepare_all(rd("RELIANCE"), rd("NIFTY"))
    rows, final = [], None
    for name, (sg, pb) in variants(d).items():
        x = d.copy()
        x["signal"], x["pb_filter"] = sg, pb
        t = book.backtest(x)
        te = pd.to_datetime(t.entry_time)
        r = {"Version": name}
        for yn, a, b in YEARS:
            g = t[(te >= a) & (te < b)]
            s = book.stats(g)
            r[yn] = f"₹{s['Net P&L Rs']:+,} (PF {s['Profit Factor']:.2f})"
        s = book.stats(t)
        r["3 saal total"] = f"₹{s['Net P&L Rs']:+,} (PF {s['Profit Factor']:.2f}, DD ₹{s['Max DD Rs']:,})"
        rows.append(r)
        if name.startswith("V5"):
            final = t
    res = pd.DataFrame(rows).set_index("Version")
    pd.set_option("display.width", 250)
    print(res.to_string())
    t = final
    te = pd.to_datetime(t.entry_time)
    print("\nFINAL saal-dar-saal:")
    for yn, a, b in YEARS:
        g = t[(te >= a) & (te < b)]
        print(f"  {yn:36s}", book.stats(g))
        for s_, gg in g.groupby("strategy"):
            print(f"      {book.NAMES[s_]:22s}", {k: v for k, v in book.stats(gg).items() if k in ('Trades', 'Win %', 'Profit Factor', 'Net P&L Rs')})
        for sd, gg in g.groupby("side"):
            print(f"      side {sd:4s}", {k: v for k, v in book.stats(gg).items() if k in ('Trades', 'Profit Factor', 'Net P&L Rs')})
    print("  3 SAAL TOTAL", book.stats(t))
    m = t.groupby(te.dt.to_period("M")).pnl_rs.agg(["count", "sum"]).round(0)
    m["account"] = 100000 + m["sum"].cumsum()
    print("\nMonthly:\n" + m.T.to_string())
    out = os.path.join(HERE, "results")
    t.to_csv(os.path.join(out, "final_3y_trades.csv"), index=False)
    with open(os.path.join(out, "final_3y_summary.md"), "w") as f:
        f.write("## Reliance 3-year backtest (Rs 1 lakh, ek position)\n\n" + res.to_markdown() + "\n\n### FINAL monthly\n\n" + m.to_markdown() + "\n")


if __name__ == "__main__":
    main()
