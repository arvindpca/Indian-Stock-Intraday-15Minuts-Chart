"""Strategy ki infographic image banata hai -> reliance_strategy/STRATEGY.png"""
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from final_strategy import prepare  # noqa: E402

DAY = "2026-10-15"
BG, CARD, INK, MUTED = "#0f1419", "#1a2129", "#e8edf2", "#8a96a3"
GREEN, RED, AMBER, BLUE = "#26a96c", "#e5484d", "#f5a524", "#4c9aff"
plt.rcParams.update({"font.family": "DejaVu Sans", "text.color": INK, "axes.labelcolor": MUTED,
                     "xtick.color": MUTED, "ytick.color": MUTED})

d = prepare(os.path.join(HERE, "data", "RELIANCE_5m.csv"))
t = pd.read_csv(os.path.join(HERE, "results", "final_strategy_trades.csv"), parse_dates=["entry_time", "exit_time"])
tr = t[t.entry_time.dt.strftime("%Y-%m-%d") == DAY].iloc[0]
x = d[(d.index.normalize() == pd.Timestamp(DAY)) & (d.tod <= 915)].copy()

# trailing SL path for the example
ei = x.index.get_loc(tr.entry_time)
R = x.atr.iloc[ei - 1]
side = 1 if tr.side == "BUY" else -1
sl = [np.nan] * len(x)
stop = tr.entry - side * 2 * R
for j in range(ei, len(x)):
    sl[j] = stop
    if x.index[j] >= tr.exit_time:
        break
    stop = max(stop, x.close.iloc[j] - 2 * R) if side == 1 else min(stop, x.close.iloc[j] + 2 * R)
x["sl"] = sl

fig = plt.figure(figsize=(12, 16), dpi=110, facecolor=BG)


def card(ax_rect, color=CARD):
    ax = fig.add_axes(ax_rect)
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    ax.add_patch(FancyBboxPatch((0, 0), 1, 1, boxstyle="round,pad=0,rounding_size=0.03",
                                fc=color, ec="none", transform=ax.transAxes))
    return ax


# ---------------- header
fig.text(0.05, 0.965, "RELIANCE INTRADAY", fontsize=30, weight="bold", color=INK)
fig.text(0.05, 0.94, "20-Minute Momentum  +  2×ATR Trailing Stop", fontsize=18, color=AMBER, weight="bold")
fig.text(0.05, 0.922, "5-minute chart  •  1 saal ke data se reverse engineering  •  Din me sirf 1 trade",
         fontsize=12, color=MUTED)

# ---------------- rules (6 cards, 3x2)
rules = [
    ("1", "ATR(14) lagao", "5-min chart pe. Ye batata hai\nReliance normally kitna hilta hai\n(maan lo ₹3)", BLUE),
    ("2", "20 min ka move dekho", "Move = (Close − 4 candle\npehle ka Close) ÷ ATR", BLUE),
    ("3", "Signal", "Move > +1.5  →  BUY\nMove < −1.5  →  SELL", AMBER),
    ("4", "Entry", "Agli candle ke OPEN pe\nSirf 9:30 – 2:30 ke beech\nDin me sirf 1 trade", GREEN),
    ("5", "Stop Loss (trailing)", "Shuru me entry se 2×ATR door\nHar candle: SL = Close − 2×ATR\n(sirf aapke favour me khiske)", RED),
    ("6", "Exit", "Koi fixed target NAHI\nTrailing SL hit  ya\n3:15 baje — jo pehle ho", MUTED),
]
w, h, gx, gy = 0.285, 0.105, 0.0225, 0.012
for k, (num, title, body, col) in enumerate(rules):
    r, c = divmod(k, 3)
    ax = card([0.05 + c * (w + gx), 0.795 - r * (h + gy), w, h])
    ax.add_patch(Rectangle((0, 0.08), 0.018, 0.84, color=col, transform=ax.transAxes))
    ax.text(0.07, 0.80, num, fontsize=20, weight="bold", color=col, va="center")
    ax.text(0.19, 0.80, title, fontsize=13.5, weight="bold", va="center")
    ax.text(0.07, 0.38, body, fontsize=11, color=INK, va="center", linespacing=1.45)

# ---------------- example chart
fig.text(0.05, 0.655, f"Asli example — {pd.Timestamp(DAY):%d %b %Y}  ({tr.side})", fontsize=16, weight="bold")
ax = fig.add_axes([0.08, 0.36, 0.87, 0.28], facecolor=CARD)
for s in ax.spines.values():
    s.set_visible(False)
ax.grid(color="#2a333d", lw=0.6)
xi = np.arange(len(x))
for i, (o, hgh, low, c) in enumerate(zip(x.open, x.high, x.low, x.close)):
    col = GREEN if c >= o else RED
    ax.vlines(i, low, hgh, color=col, lw=1)
    ax.add_patch(Rectangle((i - 0.33, min(o, c)), 0.66, max(abs(c - o), 0.15), color=col))
ax.step(xi, x.sl, where="post", color=AMBER, lw=2.2, label="Trailing SL (2×ATR)")
xo = ei
xe = x.index.get_loc(tr.exit_time)
sig_i = ei - 1
ax.axvspan(max(sig_i - 4, -0.6), sig_i + 0.4, color=BLUE, alpha=0.12)
ax.annotate(f"Signal: 20 min me\n+{x.move.iloc[sig_i]:.1f}×ATR move", (sig_i, x.high.iloc[sig_i]),
            xytext=(-0.5, 1213), fontsize=10.5, color=BLUE,
            arrowprops=dict(arrowstyle="->", color=BLUE))
ax.scatter([xo], [tr.entry], s=160, marker="^", color=GREEN, zorder=5, ec="white")
ax.annotate(f"BUY ₹{tr.entry:.1f}\nSL ₹{tr.entry - 2 * R:.1f}", (xo, tr.entry), xytext=(xo - 3, 1198),
            fontsize=11, weight="bold", color=GREEN, arrowprops=dict(arrowstyle="->", color=GREEN))
ax.scatter([xe], [tr.exit], s=160, marker="X", color=AMBER, zorder=5, ec="white")
ax.annotate(f"EXIT ₹{tr.exit:.1f}\n(trailing SL hit)", (xe, tr.exit), xytext=(xe - 20, tr.exit + 5),
            fontsize=11, weight="bold", color=AMBER, arrowprops=dict(arrowstyle="->", color=AMBER))
ticks = [i for i, ts in enumerate(x.index) if ts.minute == 0 or (ts.hour == 9 and ts.minute == 15)]
ax.set_xticks(ticks, [x.index[i].strftime("%H:%M") for i in ticks])
ax.set_xlim(-1, len(x))
ax.set_ylabel("Price ₹")
ax.legend(loc="lower right", facecolor=CARD, edgecolor="none", labelcolor=INK)
gain = tr.exit - tr.entry
fig.text(0.08, 0.318, f"Entry ₹{tr.entry:.1f}  →  Exit ₹{tr.exit:.1f}  =  ₹{gain:+.1f}/share  ({tr.ret_pct:+.2f}% charges ke baad)\n"
         "SL price ke peeche-peeche chalta raha aur profit lock karta gaya.", fontsize=11, color=MUTED, linespacing=1.5)

# ---------------- results
fig.text(0.05, 0.295, "Backtest result (10 Oct 2025 – 23 Oct 2026, charges 0.06% kaat ke)", fontsize=16, weight="bold")
stats = [("254", "Trades"), ("39%", "Win rate"), ("1.28", "Profit Factor"), ("+16.8%", "Total"),
         ("−6.3%", "Max drawdown"), ("10/13", "Profit wale mahine")]
for k, (v, lab) in enumerate(stats):
    ax = card([0.05 + k * 0.152, 0.205, 0.138, 0.075])
    ax.text(0.5, 0.62, v, fontsize=19, weight="bold", ha="center", va="center",
            color=GREEN if k in (2, 3, 5) else INK)
    ax.text(0.5, 0.24, lab, fontsize=10, ha="center", va="center", color=MUTED)

ax = card([0.05, 0.115, 0.43, 0.075])
ax.text(0.04, 0.68, "Kamai ka raaz", fontsize=12.5, weight="bold", color=GREEN)
ax.text(0.04, 0.28, "Avg jeet +0.77%  vs  avg haar −0.38%\nHaar chhoti, jeet double", fontsize=11, va="center")
ax = card([0.52, 0.115, 0.43, 0.075])
ax.text(0.04, 0.68, "Unseen test (aakhri 4 mahine)", fontsize=12.5, weight="bold", color=BLUE)
ax.text(0.04, 0.28, "86 trades • PF 1.27 • +5.5%\nRule ne ye data dekha hi nahi tha", fontsize=11, va="center")

# ---------------- warnings
ax = card([0.05, 0.02, 0.9, 0.08], color="#2a1d14")
ax.text(0.025, 0.74, "⚠  Dhyan rakho", fontsize=13, weight="bold", color=AMBER)
ax.text(0.025, 0.30,
        "• 10 me se ~6 trade chhote loss me jaate hain; lagatar 9 loss tak aaye hain   • Zyada profit SELL side se (Reliance saal bhar gira)\n"
        "• Sirf Reliance pe test — Nifty pe nahi chala   • Sasta brokerage zaroori   • Pehle 1–2 mahine PAPER TRADE, phir live",
        fontsize=10.2, va="center", linespacing=1.6)

out = os.path.join(HERE, "STRATEGY.png")
fig.savefig(out, facecolor=BG)
print("saved", out)
