"""Systematic discovery: baselines -> single trigger -> trigger+filter -> trigger+2 filters.

All ranking happens on the DEV period only. VAL is used once to accept/reject. OOS is
never touched here. Every experiment is logged (experiments.csv) and BH-FDR is applied
across ALL dev experiments to control multiple-testing bias.
"""
from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd

from research.engine import run_strategy
from research.metrics import bh_fdr, summarize

KEYS = ["trades", "net_pnl", "win_rate", "profit_factor", "expectancy_rs", "expectancy_R", "expectancy_R_before_costs", "avg_cost_R", "max_drawdown",
        "max_losing_streak", "positive_month_frac", "t_stat", "p_value", "charges", "symbols_traded"]


def exp_id(trigger, filters, side, sl, rr):
    return f"{side}|{trigger}|{'+'.join(sorted(filters)) or '-'}|sl{sl}|rr{rr}"


class Researcher:
    def __init__(self, P, cache, cfg, periods: dict, log):
        self.P, self.cache, self.cfg, self.periods, self.log = P, cache, cfg, periods, log
        self.records: list[dict] = []
        self._done: set = set()

    def evaluate(self, stage, trigger, filters, side, sl, rr, period="dev", slip_mult=1.0, keep_trades=False):
        t = run_strategy(self.P, self.cache, self.cfg, trigger, list(filters), side, sl, rr,
                         row_mask=self.periods[period], slip_mult=slip_mult)
        s = summarize(t, self.cfg["risk"]["capital"])
        rec = {"id": exp_id(trigger, filters, side, sl, rr), "stage": stage, "period": period, "side": side,
               "trigger": trigger, "filters": "+".join(filters) or "-", "sl_atr": sl, "rr": rr, "slip_mult": slip_mult}
        rec.update({k: s.get(k, np.nan) for k in KEYS})
        return (rec, t) if keep_trades else rec

    def _log(self, rec):
        if rec["id"] + rec["period"] + rec["stage"] not in self._done:
            self.records.append(rec)
            self._done.add(rec["id"] + rec["period"] + rec["stage"])

    def frame(self):
        return pd.DataFrame(self.records)

    # ---------------------------------------------------------------- stages
    def run(self, triggers, filters):
        dc, ex = self.cfg["discovery"], self.cfg["exits"]
        min_tr = dc["min_trades_dev"]
        best = {}
        # Stage 1 : each trigger alone (incl. OPEN = no-filter and NBAR_BRK = price-action baselines)
        for side in "LS":
            for trig in triggers:
                for sl in ex["sl_atr"]:
                    for rr in ex["rr"]:
                        self._log(self.evaluate("1_single", trig, [], side, sl, rr))
        df = self.frame()
        s1 = df[(df.stage == "1_single") & (df.trades >= min_tr)]
        self.log.info("stage1: %d experiments, %d with >= %d trades", (df.stage == "1_single").sum(), len(s1), min_tr)
        # Stage 2 : top triggers (with their best exit on dev) x each filter
        for side in "LS":
            top = (s1[s1.side == side].sort_values("expectancy_R", ascending=False)
                   .drop_duplicates("trigger").head(dc["top_triggers"]))
            for r in top.itertuples():
                best[(side, r.trigger)] = (r.sl_atr, r.rr, r.expectancy_R)
                for fl in filters:
                    rec = self.evaluate("2_pair", r.trigger, [fl], side, r.sl_atr, r.rr)
                    rec["delta_expR_vs_trigger"] = rec["expectancy_R"] - r.expectancy_R if rec["trades"] else np.nan
                    self._log(rec)
        df = self.frame()
        s2 = df[(df.stage == "2_pair") & (df.trades >= min_tr) & (df.delta_expR_vs_trigger > 0)]
        self.log.info("stage2: %d experiments, %d improve on trigger alone", (df.stage == "2_pair").sum(), len(s2))
        # Stage 3 : best pairs x a second filter
        for side in "LS":
            top = s2[s2.side == side].sort_values("expectancy_R", ascending=False).head(dc["top_pairs"])
            for r in top.itertuples():
                for fl in filters:
                    if fl == r.filters:
                        continue
                    rec = self.evaluate("3_multi", r.trigger, [r.filters, fl], side, r.sl_atr, r.rr)
                    rec["delta_expR_vs_trigger"] = rec["expectancy_R"] - best[(side, r.trigger)][2] if rec["trades"] else np.nan
                    rec["delta_expR_vs_pair"] = rec["expectancy_R"] - r.expectancy_R if rec["trades"] else np.nan
                    self._log(rec)
        df = self.frame()
        df["fdr_significant"] = bh_fdr(df["p_value"].to_numpy(), dc["fdr_alpha"])
        self.records = df.to_dict("records")
        self.log.info("total dev experiments: %d | FDR-significant (alpha=%.2f): %d", len(df), dc["fdr_alpha"], int(df.fdr_significant.sum()))
        return df

    def validate(self, dev: pd.DataFrame):
        """Pick finalists on DEV, accept/reject on VAL (+ dev/val month stability)."""
        dc = self.cfg["discovery"]
        rows = []
        for side in "LS":
            cand = (dev[(dev.side == side) & (dev.trades >= dc["min_trades_dev"]) & (dev.expectancy_R > 0)]
                    .sort_values("expectancy_R", ascending=False).drop_duplicates("id").head(dc["finalists"] * 3))
            for r in cand.itertuples():
                filt = [] if r.filters == "-" else r.filters.split("+")
                v = self.evaluate("val", r.trigger, filt, side, r.sl_atr, r.rr, period="val")
                both = self.evaluate("devval", r.trigger, filt, side, r.sl_atr, r.rr, period="devval")
                reasons = []
                if v["trades"] < dc["min_trades_val"]:
                    reasons.append(f"val trades {v['trades']} < {dc['min_trades_val']}")
                if not (v.get("expectancy_R", -1) > 0):
                    reasons.append("val expectancy <= 0")
                if not (v.get("profit_factor", 0) > 1.05):
                    reasons.append("val PF <= 1.05")
                if not (both.get("positive_month_frac", 0) >= dc["min_positive_month_frac"]):
                    reasons.append("too few positive months (dev+val)")
                if not r.fdr_significant:
                    reasons.append("dev result not FDR-significant (possible luck)")
                rows.append({"id": r.id, "side": side, "trigger": r.trigger, "filters": r.filters, "sl_atr": r.sl_atr,
                             "rr": r.rr, "dev_trades": r.trades, "dev_expR": r.expectancy_R, "dev_PF": r.profit_factor,
                             "dev_fdr_sig": bool(r.fdr_significant), "val_trades": v["trades"],
                             "val_expR": v.get("expectancy_R"), "val_PF": v.get("profit_factor"),
                             "val_net": v.get("net_pnl"), "devval_pos_month_frac": both.get("positive_month_frac"),
                             "accepted": len([x for x in reasons if "FDR" not in x]) == 0,
                             "strict_accepted": len(reasons) == 0, "reject_reasons": "; ".join(reasons)})
        out = pd.DataFrame(rows)
        if len(out):
            out = out.sort_values(["accepted", "val_expR"], ascending=[False, False])
        return out


def config_hash(cfg: dict, extra: str = "") -> str:
    return hashlib.sha1((json.dumps(cfg, sort_keys=True, default=str) + extra).encode()).hexdigest()[:10]
