"""Angel One NSE equity INTRADAY charges (vectorised). All rates from config.yaml."""
from __future__ import annotations

import numpy as np


def intraday_charges(buy_value, sell_value, c: dict):
    """Total statutory + broker charges for one round trip (Rs). Works on scalars or arrays."""
    buy_value, sell_value = np.asarray(buy_value, float), np.asarray(sell_value, float)

    def brok(v):
        return np.maximum(c["brokerage_min"], np.minimum(c["brokerage_cap"], v * c["brokerage_pct"] / 100))

    brokerage = brok(buy_value) + brok(sell_value)
    turnover = buy_value + sell_value
    stt = sell_value * c["stt_sell_pct"] / 100
    exch = turnover * c["exchange_pct"] / 100
    sebi = turnover * c["sebi_per_crore"] / 1e7
    ipft = turnover * c["ipft_pct"] / 100
    stamp = buy_value * c["stamp_buy_pct"] / 100
    gst = (brokerage + exch + sebi + ipft) * c["gst_pct"] / 100
    return brokerage + stt + exch + sebi + ipft + stamp + gst


def breakdown(buy_value: float, sell_value: float, c: dict) -> dict:
    def brok(v):
        return max(c["brokerage_min"], min(c["brokerage_cap"], v * c["brokerage_pct"] / 100))
    t = buy_value + sell_value
    b = brok(buy_value) + brok(sell_value)
    exch, sebi, ipft = t * c["exchange_pct"] / 100, t * c["sebi_per_crore"] / 1e7, t * c["ipft_pct"] / 100
    d = {"brokerage": b, "stt": sell_value * c["stt_sell_pct"] / 100, "exchange": exch, "sebi": sebi, "ipft": ipft,
         "stamp": buy_value * c["stamp_buy_pct"] / 100, "gst": (b + exch + sebi + ipft) * c["gst_pct"] / 100}
    d["total"] = sum(d.values())
    return d
