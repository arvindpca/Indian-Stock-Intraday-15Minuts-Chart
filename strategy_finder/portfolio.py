#!/usr/bin/env python3
"""Portfolio simulation: Rs 50,000 per trade, max 5 positions open at the same time.
A: SMC SHORT (live paper-trading rules: 10:00+, SL 1.5%, TP 2%, OB only, RSI off)
B: VWAP-pullback fade SHORT (strong stock > +1% & > Nifty +1.2%, Nifty up last 15m,
   candle dips to VWAP and closes above -> SHORT; SL 1%, TP 2%)
A+B together share the same 5 slots.  Charges 0.10% per trade deducted."""
import glob
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backtest_v1_smc_5m as bt          # noqa: E402
import finder as f1                       # noqa: E402
import finder2 as f2                      # noqa: E402

ROOT = f1.ROOT
OUT = ROOT / "data" / "portfolio"
OUT.mkdir(parents=True, exist_ok=True)
PER_TRADE, MAX_POS, COST = 50000.0, 5, 0.10


def smc_short_trades():
    bt.ENTRY_START_MIN, bt.STOP_LOSS_PERCENT, bt.TP_PERCENT = 600, 1.5, 2.0
    bt.USE_FVG, bt.USE_RSI_FILTER = False, False
    rows = []
    for f in sorted(glob.glob(str(ROOT / "data" / "angel_5m" / "*_5m.csv"))):
        df = bt.load_csv(f)
        if len(df) < 100:
            continue
        d = bt.add_indicators(df)
        sym = Path(f).name.replace("_5m.csv", "")
        for s in bt.find_signals(d):
            if s["direction"] == -1:
                t = bt.simulate(d, s)
                rows.append({"strategy": "SMC_SHORT", "symbol": sym, "entry_time": t["entry_time"],
                             "exit_time": t["exit_time"], "result": t["result"], "net_pct": t["net_pct"]})
    return pd.DataFrame(rows)


def vwap_fade_trades(sl=1.0, tp=2.0):
    nifty = f1.load_nifty()
    rows = []
    for path in sorted(glob.glob(str(ROOT / "data" / "angel_5m" / "*_5m.csv"))):
        d = f1.load_stock(path)
        if len(d) < 300:
            continue
        f = f1.features(d, nifty)
        ev = f2.events(d, f)["PULLBACK_UPTREND"].to_numpy()
        ok = ev & (f["n_ret_15m"].fillna(-999).to_numpy() > 0.139) & (f["rel_strength"].fillna(-999).to_numpy() > 1.2) \
            & (f["turnover_lakh"].fillna(0).to_numpy() >= f1.LIQ_MIN_LAKH)
        H, L, C, M, D = (d["high"].to_numpy(), d["low"].to_numpy(), d["close"].to_numpy(),
                         d["minute"].to_numpy(), d["date"].to_numpy())
        sym = Path(path).name.replace("_5m.csv", "")
        for i in np.where(ok)[0]:
            e = C[i]
            tp_lv, sl_lv = e * (1 - tp / 100), e * (1 + sl / 100)
            j, res, px = i, "EOD", None
            for j in range(i + 1, len(d)):
                if D[j] != D[i]:
                    j -= 1
                    break
                if H[j] >= sl_lv:                       # stop first (same rule as the finder)
                    res, px = "SL", sl_lv
                    break
                if L[j] <= tp_lv:
                    res, px = "TP", tp_lv
                    break
                if M[j] >= f1.EOD:
                    break
            if px is None:
                px = C[j]
            rows.append({"strategy": "VWAP_FADE_SHORT", "symbol": sym,
                         "entry_time": d["time"].iat[i].strftime("%Y-%m-%d %H:%M"),
                         "exit_time": d["time"].iat[j].strftime("%Y-%m-%d %H:%M"), "result": res,
                         "net_pct": round((1 - px / e) * 100 - COST, 3)})
    return pd.DataFrame(rows)


def run_book(trades):
    """take signals in time order; skip if 5 already open or the stock is already open"""
    t = trades.sort_values(["entry_time", "symbol"]).reset_index(drop=True)
    open_pos, taken, skipped = [], [], 0
    for _, r in t.iterrows():
        open_pos = [p for p in open_pos if p["exit_time"] > r["entry_time"]]
        if len(open_pos) >= MAX_POS or any(p["symbol"] == r["symbol"] for p in open_pos):
            skipped += 1
            continue
        open_pos.append(r)
        taken.append(r)
    b = pd.DataFrame(taken)
    b["pnl_rs"] = b["net_pct"] / 100 * PER_TRADE
    b["month"] = b["entry_time"].str[:7]
    b["date"] = b["entry_time"].str[:10]
    daily = b.groupby("date")["pnl_rs"].sum()
    eq = daily.cumsum()
    split = "2026-09-01"
    out = {
        "signals": len(t), "taken": len(b), "skipped_5_full": skipped,
        "win_rate_pct": round((b["pnl_rs"] > 0).mean() * 100, 1),
        "tp": int((b["result"] == "TP").sum()), "sl": int((b["result"] == "SL").sum()),
        "eod": int((b["result"] == "EOD").sum()),
        "total_rs": round(b["pnl_rs"].sum()), "return_on_2_5L_pct": round(b["pnl_rs"].sum() / (PER_TRADE * MAX_POS) * 100, 1),
        "apr_aug_rs": round(b[b["date"] < split]["pnl_rs"].sum()), "sep_oct_rs": round(b[b["date"] >= split]["pnl_rs"].sum()),
        "max_drawdown_rs": round(float((eq - eq.cummax()).min())),
        "trading_days": int(daily.size), "green_days": int((daily > 0).sum()), "red_days": int((daily <= 0).sum()),
        "best_day_rs": round(daily.max()), "worst_day_rs": round(daily.min()),
        "monthly_rs": {k: round(v) for k, v in b.groupby("month")["pnl_rs"].sum().items()},
        "monthly_trades": {k: int(v) for k, v in b.groupby("month").size().items()},
        "max_open_at_once": MAX_POS,
    }
    return out, b


def main():
    a, b = smc_short_trades(), vwap_fade_trades()
    res = {}
    for name, tr in (("SMC_SHORT", a), ("VWAP_FADE_SHORT", b), ("BOTH", pd.concat([a, b], ignore_index=True))):
        r, book = run_book(tr)
        res[name] = r
        book.to_csv(OUT / f"book_{name}.csv", index=False)
    (OUT / "portfolio.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
