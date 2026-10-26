"""Run:  python -m unittest discover -s research/tests -v   (from repo root)"""
import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from research.common import ROOT, load_config  # noqa: E402
from research.costs import breakdown, intraday_charges  # noqa: E402
from research.data_quality import clean_and_validate, resample_15m  # noqa: E402
from research.engine import (EOD, SL_HIT, TP_HIT, ExitCache, build_panel, portfolio_sim,  # noqa: E402
                             precompute_exits, run_strategy, select_non_overlapping)
from research.features import build_features  # noqa: E402
from research.metrics import bh_fdr, equity_drawdown, max_losing_streak, summarize  # noqa: E402

CFG = load_config()


def synth_day(date="2025-01-06", base=100.0, n=75):
    """Flat synthetic day: every bar o=h=l=c=base, ATR=1."""
    t = pd.date_range(f"{date} 09:15", periods=n, freq="5min")
    f = pd.DataFrame({"datetime": t, "open": base, "high": base, "low": base, "close": base, "volume": 1e6})
    f["date"] = f["datetime"].dt.normalize()
    f["slot"] = np.arange(n)
    f["atr"] = 1.0
    f["avgvol20"] = 1e6
    f["no_trade_day"] = False
    f["vol_regime"] = "na"
    f["T_X_L"] = False
    f["T_X_S"] = False
    f["F_Y_L"] = True
    f["F_Y_S"] = True
    return f


def panel_of(f):
    return build_panel({"SYN": f}, CFG)


class TestExecution(unittest.TestCase):
    def test_entry_is_next_bar_open(self):
        f = synth_day()
        f.loc[10, "T_X_L"] = True
        f.loc[11, "open"] = 101.0          # next bar open differs from signal close
        f.loc[11, ["high", "low", "close"]] = [101.0, 101.0, 101.0]
        P = panel_of(f)
        t = run_strategy(P, ExitCache(P), CFG, "X", [], "L", 1.0, 0)
        self.assertEqual(len(t), 1)
        self.assertEqual(t.loc[0, "entry_slot"], 11)
        self.assertAlmostEqual(t.loc[0, "entry_raw"], 101.0)
        self.assertNotAlmostEqual(t.loc[0, "entry_raw"], f.loc[10, "close"])

    def test_sl_and_tp_same_bar_assumes_sl(self):
        f = synth_day()
        f.loc[10, "T_X_L"] = True
        f.loc[13, ["high", "low"]] = [110.0, 90.0]     # hits both SL (99) and TP (102)
        P = panel_of(f)
        xs, xp, rs = precompute_exits(P, 1, 1.0, 2.0)
        self.assertEqual(rs[0, 10], SL_HIT)
        self.assertEqual(xs[0, 10], 13)
        self.assertAlmostEqual(xp[0, 10], 99.0)

    def test_tp_before_sl(self):
        f = synth_day()
        f.loc[10, "T_X_L"] = True
        f.loc[12, "high"] = 102.5
        f.loc[14, "low"] = 98.0
        P = panel_of(f)
        xs, xp, rs = precompute_exits(P, 1, 1.0, 2.0)
        self.assertEqual((rs[0, 10], xs[0, 10]), (TP_HIT, 12))
        self.assertAlmostEqual(xp[0, 10], 102.0)

    def test_gap_through_stop_fills_at_open(self):
        f = synth_day()
        f.loc[10, "T_X_S"] = True
        f.loc[15, ["open", "high", "low", "close"]] = [105.0, 105.0, 104.0, 104.0]   # short SL=101, opens at 105
        P = panel_of(f)
        xs, xp, rs = precompute_exits(P, -1, 1.0, 0)
        self.assertEqual(rs[0, 10], SL_HIT)
        self.assertAlmostEqual(xp[0, 10], 105.0)

    def test_eod_exit_at_square_off_open(self):
        f = synth_day()
        f.loc[10, "T_X_L"] = True
        sq = panel_of(f).sq
        f.loc[sq, "open"] = 100.5
        f.loc[sq, "high"] = 100.5
        P = panel_of(f)
        xs, xp, rs = precompute_exits(P, 1, 1.0, 0)
        self.assertEqual((rs[0, 10], xs[0, 10]), (EOD, sq))
        self.assertAlmostEqual(xp[0, 10], 100.5)

    def test_no_entry_outside_window(self):
        f = synth_day()
        f.loc[[1, 67], "T_X_L"] = True      # 09:20 and 14:50 -> both outside 09:30..14:40
        P = panel_of(f)
        t = run_strategy(P, ExitCache(P), CFG, "X", [], "L", 1.0, 0)
        self.assertEqual(len(t), 0)

    def test_no_overlapping_positions(self):
        f = synth_day()
        f.loc[[10, 12, 14], "T_X_L"] = True
        f.loc[20, "low"] = 98.0             # first trade stopped at bar 20
        f.loc[25, "T_X_L"] = True
        P = panel_of(f)
        xs, _, _ = precompute_exits(P, 1, 1.0, 0)
        m = P.feats["T_X_L"] & P.tradable
        rows, slots = select_non_overlapping(m, xs, P.js, P.je)
        self.assertEqual(list(slots), [10, 25])

    def test_portfolio_limits(self):
        base = pd.Timestamp("2025-01-06 10:00")
        rows = []
        for i in range(8):
            rows.append({"symbol": f"S{i}", "date": pd.Timestamp("2025-01-06"), "direction": "LONG",
                         "entry_time": base + pd.Timedelta(minutes=i), "exit_time": base + pd.Timedelta(hours=2),
                         "net": -100.0})
        acc = portfolio_sim(pd.DataFrame(rows), CFG)
        self.assertEqual(len(acc), CFG["risk"]["max_positions"])
        loss = [{"symbol": "A", "date": pd.Timestamp("2025-01-06"), "direction": "LONG", "entry_time": base,
                 "exit_time": base + pd.Timedelta(minutes=5), "net": -5000.0},
                {"symbol": "B", "date": pd.Timestamp("2025-01-06"), "direction": "LONG",
                 "entry_time": base + pd.Timedelta(minutes=30), "exit_time": base + pd.Timedelta(minutes=60), "net": 50.0}]
        self.assertEqual(len(portfolio_sim(pd.DataFrame(loss), CFG)), 1)   # daily loss limit blocks 2nd


class TestCosts(unittest.TestCase):
    def test_round_trip_hand_calc(self):
        c = CFG["costs"]
        buy, sell = 100000.0, 101000.0
        exp_brok = 20 + 20
        turnover = buy + sell
        exch = turnover * 0.0030699 / 100
        sebi = turnover * 10 / 1e7
        ipft = turnover * 0.0000001 / 100
        expected = exp_brok + sell * 0.025 / 100 + exch + sebi + ipft + buy * 0.003 / 100 + 0.18 * (exp_brok + exch + sebi + ipft)
        self.assertAlmostEqual(float(intraday_charges(buy, sell, c)), expected, places=6)
        self.assertAlmostEqual(breakdown(buy, sell, c)["total"], expected, places=6)

    def test_min_brokerage(self):
        c = CFG["costs"]
        b = breakdown(2000.0, 2000.0, c)
        self.assertAlmostEqual(b["brokerage"], 10.0)       # 0.1% = Rs2 -> minimum Rs5 per order

    def test_short_trade_stt_on_sell_leg(self):
        f = synth_day()
        f.loc[10, "T_X_S"] = True
        P = panel_of(f)
        t = run_strategy(P, ExitCache(P), CFG, "X", [], "S", 1.0, 0)
        self.assertEqual(len(t), 1)
        self.assertLess(t.loc[0, "net"], 0)                # flat price -> costs + slippage make it a loss
        self.assertGreater(t.loc[0, "charges"], 0)


class TestMetrics(unittest.TestCase):
    def test_summary(self):
        net = [100, -50, -50, 200, -100]
        t = pd.DataFrame({"net": net, "gross": net, "charges": 0.0, "slippage": 0.0, "R": np.array(net) / 100,
                          "date": pd.date_range("2025-01-01", periods=5), "exit_time": pd.date_range("2025-01-01", periods=5),
                          "entry_slot": 10, "exit_slot": 12, "symbol": "A"})
        s = summarize(t, capital=1000)
        self.assertAlmostEqual(s["profit_factor"], 1.5)
        self.assertAlmostEqual(s["win_rate"], 40.0)
        self.assertEqual(s["max_losing_streak"], 2)
        self.assertAlmostEqual(s["max_drawdown"], -100.0)
        self.assertAlmostEqual(s["expectancy_rs"], 20.0)
        self.assertEqual(max_losing_streak([-1, -1, -1, 1, -1]), 3)
        self.assertAlmostEqual(equity_drawdown(t, 1000)["equity"].iloc[-1], 1100.0)

    def test_bh_fdr(self):
        p = np.array([0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205, 0.212, 0.216, np.nan])
        sig = bh_fdr(p, 0.05)
        self.assertEqual(sig.sum(), 2)
        self.assertFalse(sig[-1])


class TestDataQuality(unittest.TestCase):
    def test_duplicates_invalid_and_session(self):
        t = list(pd.date_range("2026-10-19 09:15", periods=75, freq="5min"))
        df = pd.DataFrame({"datetime": t, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0, "volume": 100})
        df = pd.concat([df, df.iloc[[5]]])                                         # duplicate
        df = pd.concat([df, pd.DataFrame([{"datetime": pd.Timestamp("2026-10-19 16:00"), "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1}])])
        df.iloc[3, df.columns.get_loc("high")] = 8.0                                # invalid (high < open)
        clean, rep = clean_and_validate(df, "X", CFG, is_index=True)
        self.assertEqual(rep["dup_ts"], 1)
        self.assertEqual(rep["invalid_ohlc"], 1)
        self.assertEqual(rep["out_of_session"], 1)
        self.assertEqual(len(clean), 74)
        self.assertEqual(rep["missing_bars"], 1)                                   # dropped bar NOT filled

    def test_resample_15m_anchor(self):
        t = pd.date_range("2026-10-19 09:15", periods=6, freq="5min")
        df = pd.DataFrame({"datetime": t, "open": [1, 2, 3, 4, 5, 6], "high": [2, 3, 9, 5, 6, 7], "low": [0, 1, 2, 3, 4, 5],
                           "close": [2, 3, 4, 5, 6, 7], "volume": [1, 1, 1, 2, 2, 2]})
        r = resample_15m(df)
        self.assertEqual(list(r["datetime"].dt.strftime("%H:%M")), ["09:15", "09:30"])
        self.assertEqual(list(r["high"]), [9, 7])
        self.assertEqual(list(r["volume"]), [3, 6])


class TestNoLookahead(unittest.TestCase):
    """Features at bar t must be identical whether or not future bars exist."""

    @classmethod
    def setUpClass(cls):
        p = ROOT / "reliance_strategy/data/RELIANCE_5m_3y.csv"
        m = ROOT / "reliance_strategy/data/NIFTY_5m_3y.csv"
        if not p.exists():
            raise unittest.SkipTest("RELIANCE seed data not in repo")
        df = pd.read_csv(p, parse_dates=["datetime"]).tail(75 * 90).reset_index(drop=True)
        df["no_trade_day"] = False
        cls.df = df
        cls.mkt = pd.read_csv(m, parse_dates=["datetime"]) if m.exists() else None
        cls.full = build_features(df, CFG, cls.mkt)

    def test_truncation_invariance(self):
        rng = np.random.default_rng(7)
        cuts = sorted(rng.choice(np.arange(75 * 30, len(self.df) - 5), size=6, replace=False))
        cols = [c for c in self.full.columns if c[:2] in ("T_", "F_")] + ["atr", "avgvol20"]
        for cut in cuts:
            part = build_features(self.df.iloc[:cut + 1], CFG, self.mkt)
            a, b = part[cols].iloc[-1], self.full[cols].iloc[cut]
            for c in cols:
                va, vb = a[c], b[c]
                if isinstance(va, (float, np.floating)) and np.isnan(va):
                    self.assertTrue(np.isnan(vb), f"{c} at {cut}")
                else:
                    self.assertAlmostEqual(float(va), float(vb), places=6, msg=f"lookahead in {c} at row {cut}")


if __name__ == "__main__":
    unittest.main()
