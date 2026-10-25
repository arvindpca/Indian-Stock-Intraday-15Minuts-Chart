"""
Reliance PAPER TRADE (live) -- 20-Minute Momentum + 2-ATR Trailing Stop
-----------------------------------------------------------------------
GitHub Actions har 5 minute (market time) isko chalata hai. Har run me:
  1. Angel One se Reliance ke 5-min candle (pichle kuch din + aaj) laata hai
     -- sirf POORI band ho chuki candles use hoti hain.
  2. Aaj ka din shuru se dobara simulate karta hai (final_strategy.py wale hi rules),
     isliye agar koi run late ho ya miss ho jaye, tab bhi result same rehta hai.
  3. Jo naya hua (entry / SL khiska / exit) uska Telegram alert bhejta hai.
  4. Exit hone pe trade reliance_strategy/paper/paper_trades.csv me likhta hai.

Koi asli order nahi lagta. Entry price = signal ke baad wali candle ka open (backtest jaisa).

Local test (purane data pe ek din replay):
  python reliance_strategy/paper_live.py --replay 2026-10-14
"""
import argparse
import json
import os
import sys
import time
import unicodedata
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from final_strategy import COST, EXIT, LAST_ENTRY, START, TRAIL_ATR, prepare_df  # noqa: E402

PAPER = os.path.join(HERE, "paper")
STATE = os.path.join(PAPER, "state.json")
TRADES = os.path.join(PAPER, "paper_trades.csv")
QTY = int(os.getenv("PAPER_QTY", "100"))          # paper quantity (sirf rupee P&L dikhane ke liye)
SL_ALERT_STEP = 1.0                               # SL itna (x ATR) khiske tabhi update alert


def _env(name):
    return "".join(ch for ch in os.getenv(name, "").strip() if unicodedata.category(ch) != "Cf")


def ist_now():
    return datetime.utcnow() + timedelta(hours=5, minutes=30)


# ------------------------------------------------------------------ data
def fetch_angel(now):
    from SmartApi import SmartConnect
    import pyotp

    api = SmartConnect(api_key=_env("ANGEL_API_KEY"))
    login = api.generateSession(_env("ANGEL_CLIENT_ID"), _env("ANGEL_PASSWORD"),
                                pyotp.TOTP(_env("ANGEL_TOTP_SECRET")).now())
    if not login or not login.get("status"):
        sys.exit(f"Angel One login fail: {login and login.get('message')}")
    params = {"exchange": "NSE", "symboltoken": "2885", "interval": "FIVE_MINUTE",
              "fromdate": (now - timedelta(days=7)).strftime("%Y-%m-%d 09:15"),
              "todate": now.strftime("%Y-%m-%d %H:%M")}
    for attempt in range(3):
        try:
            r = api.getCandleData(params)
            rows = (r or {}).get("data") or []
            if rows:
                break
        except Exception as e:
            print("candle fetch retry:", e)
        time.sleep(3)
    else:
        sys.exit("Angel One se candle nahi mile")
    d = pd.DataFrame(rows, columns=["datetime", "open", "high", "low", "close", "volume"])
    d["datetime"] = pd.to_datetime(d["datetime"]).dt.tz_localize(None)
    return d.drop_duplicates("datetime").set_index("datetime").sort_index()


def completed(d, now):
    """Sirf wo candles jo poori band ho chuki hain (start + 5 min <= abhi)."""
    return d[d.index + pd.Timedelta(minutes=5) <= now]


# -------------------------------------------------------------- simulate
def simulate_today(d, today):
    """Aaj ke din ko rules ke hisaab se chalao. Return: dict ya None (aaj signal nahi)."""
    t = d[d.day == pd.Timestamp(today)]
    if t.empty:
        return None
    o, h, l, c, a = t.open.values, t.high.values, t.low.values, t.close.values, t.atr.values
    tod, sig, idx = t.tod.values, t.signal.values, t.index
    for i in range(len(t)):
        s = sig[i]
        if not (s and START <= tod[i] <= LAST_ENTRY):
            continue
        if i + 1 >= len(t):           # signal aaya, entry candle abhi band nahi hui
            return dict(status="pending", side=int(s), signal_time=str(idx[i]))
        e, R = o[i + 1], a[i]
        stop = e - s * TRAIL_ATR * R
        tr = dict(status="open", side=int(s), signal_time=str(idx[i]), entry_time=str(idx[i + 1]),
                  entry=round(float(e), 2), atr=round(float(R), 2), init_sl=round(float(stop), 2))
        for j in range(i + 1, len(t)):
            if (s == 1 and l[j] <= stop) or (s == -1 and h[j] >= stop):
                tr.update(status="closed", exit=round(float(stop), 2), exit_time=str(idx[j]), reason="Trailing SL hit")
                break
            stop = max(stop, c[j] - TRAIL_ATR * R) if s == 1 else min(stop, c[j] + TRAIL_ATR * R)
            if tod[j] >= EXIT:
                tr.update(status="closed", exit=round(float(c[j]), 2), exit_time=str(idx[j]), reason="15:15 time exit")
                break
        tr["sl"] = round(float(stop), 2)
        tr["last_close"] = round(float(c[-1]), 2)
        tr["last_time"] = str(idx[-1])
        return tr
    return None


# ----------------------------------------------------------- telegram
def tg(text, dry=False):
    print("TELEGRAM >>", text.replace("\n", " | "))
    tok, chat = _env("TELEGRAM_BOT_TOKEN"), _env("TELEGRAM_CHAT_ID")
    if dry or not (tok and chat):
        return
    import requests
    try:
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      data={"chat_id": chat, "text": text, "parse_mode": "HTML"}, timeout=15)
    except Exception as e:
        print("Telegram fail:", e)


def hhmm(ts):
    return pd.Timestamp(ts).strftime("%H:%M")


def side_txt(s):
    return "🟢 BUY" if s == 1 else "🔴 SELL"


# --------------------------------------------------------------- state
def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(st):
    os.makedirs(PAPER, exist_ok=True)
    with open(STATE, "w") as f:
        json.dump(st, f, indent=2)


def record_trade(tr, today):
    os.makedirs(PAPER, exist_ok=True)
    s = tr["side"]
    ret = s * (tr["exit"] - tr["entry"]) / tr["entry"] - COST
    row = dict(date=str(today), side="BUY" if s == 1 else "SELL", entry_time=tr["entry_time"], entry=tr["entry"],
               exit_time=tr["exit_time"], exit=tr["exit"], reason=tr["reason"],
               ret_pct=round(ret * 100, 3), pnl_rs=round(ret * tr["entry"] * QTY, 2), qty=QTY)
    df = pd.read_csv(TRADES) if os.path.exists(TRADES) else pd.DataFrame()
    if not df.empty and ((df.date == row["date"]).any()):
        return df, row
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    df.to_csv(TRADES, index=False)
    return df, row


def write_status(df):
    lines = ["## Reliance paper trade status\n"]
    if df is None or df.empty:
        lines.append("Abhi tak koi paper trade band nahi hua.\n")
    else:
        r = df.ret_pct
        pf = r[r > 0].sum() / -r[r < 0].sum() if (r < 0).any() else float("inf")
        lines.append(f"Trades: {len(df)} | Win: {(r > 0).mean() * 100:.0f}% | Total: {r.sum():+.2f}% "
                     f"| P&L: ₹{df.pnl_rs.sum():+,.0f} (qty {QTY}) | Profit Factor: {pf:.2f}\n\n")
        lines.append(df.tail(15).to_markdown(index=False) + "\n")
    with open(os.path.join(PAPER, "STATUS.md"), "w") as f:
        f.write("".join(lines))


# ------------------------------------------------------------------ main
def run(raw, now, dry=False):
    today = now.date()
    d = completed(raw, now)
    if d.empty or d.index[-1].date() != today:
        print(f"{now:%Y-%m-%d %H:%M} -- aaj ka koi candle nahi (holiday / market abhi khula nahi).")
        return
    d = prepare_df(d)
    tr = simulate_today(d, today)
    st = load_state()
    if st.get("date") != str(today):
        st = {"date": str(today)}
    last = d.iloc[-1]
    print(f"{now:%H:%M} IST | last candle {d.index[-1]:%H:%M} close {last.close:.2f} "
          f"move {last.move:+.2f} ATR | trade: {tr and tr['status']}")

    changed = False
    if tr and tr["status"] in ("open", "closed") and not st.get("entry_sent"):
        risk = abs(tr["entry"] - tr["init_sl"])
        tg(f"<b>RELIANCE PAPER {side_txt(tr['side'])}</b>\n"
           f"Entry: ₹{tr['entry']} ({hhmm(tr['entry_time'])})\n"
           f"Stop loss: ₹{tr['init_sl']} (risk ₹{risk:.2f}/share)\n"
           f"Target: nahi -- trailing SL (2×ATR = ₹{TRAIL_ATR * tr['atr']:.2f})\n"
           f"Signal candle: {hhmm(tr['signal_time'])} | 20-min momentum strategy", dry)
        st.update(entry_sent=True, side=tr["side"], entry=tr["entry"], last_sl_sent=tr["init_sl"])
        changed = True

    if tr and tr["status"] == "open" and st.get("entry_sent"):
        if abs(tr["sl"] - st.get("last_sl_sent", tr["init_sl"])) >= SL_ALERT_STEP * tr["atr"]:
            open_pnl = tr["side"] * (tr["last_close"] - tr["entry"])
            locked = tr["side"] * (tr["sl"] - tr["entry"])
            tg(f"<b>RELIANCE PAPER SL update</b> {side_txt(tr['side'])}\n"
               f"Naya SL: ₹{tr['sl']} (pehle ₹{st.get('last_sl_sent')})\n"
               f"Abhi price: ₹{tr['last_close']} | Open P&L: ₹{open_pnl:+.2f}/share\n"
               f"{'Profit lock: ₹%+.2f/share' % locked if locked > 0 else 'Abhi profit lock nahi'}", dry)
            st["last_sl_sent"] = tr["sl"]
            changed = True

    if tr and tr["status"] == "closed" and not st.get("exit_sent"):
        df, row = record_trade(tr, today)
        r = df.ret_pct
        tg(f"<b>RELIANCE PAPER EXIT</b> {side_txt(tr['side'])}\n"
           f"Entry ₹{tr['entry']} → Exit ₹{tr['exit']} ({hhmm(tr['exit_time'])})\n"
           f"Wajah: {tr['reason']}\n"
           f"Result: {row['ret_pct']:+.2f}% (charges ke baad) | ₹{row['pnl_rs']:+,.0f} on {QTY} shares\n"
           f"Ab tak: {len(df)} trades, {(r > 0).mean() * 100:.0f}% win, total {r.sum():+.2f}%", dry)
        write_status(df)
        st["exit_sent"] = True
        changed = True

    if not tr and not st.get("noday_sent") and last.tod >= EXIT:
        tg("RELIANCE paper: aaj koi signal nahi aaya (9:30–14:30 me 20-min move 1.5×ATR se kam raha).", dry)
        st["noday_sent"] = True
        changed = True

    if changed or not os.path.exists(STATE):
        save_state(st)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hello", action="store_true", help="Telegram pe 'paper trade active' test message")
    ap.add_argument("--replay", help="YYYY-MM-DD: data/RELIANCE_5m.csv pe ek din har 5 min step karke test")
    args = ap.parse_args()
    if args.hello:
        tg("<b>✅ RELIANCE PAPER TRADE ACTIVE</b>\n"
           "Strategy: 20-min momentum + 2×ATR trailing SL (5-min chart)\n"
           "BUY: 20 min me move > +1.5×ATR | SELL: < −1.5×ATR\n"
           "Entry 9:30–14:30, din me 1 trade, koi target nahi, 15:15 pe exit\n"
           f"Paper qty: {QTY} shares | Har 5 min check (Mon–Fri)\n"
           "Alerts: Entry, SL update, Exit, aur 'no signal' din")
        return
    if args.replay:
        global PAPER, STATE, TRADES
        PAPER = os.path.join("/tmp", "reliance_paper_replay")
        STATE, TRADES = os.path.join(PAPER, "state.json"), os.path.join(PAPER, "paper_trades.csv")
        if os.path.exists(STATE):
            os.remove(STATE)
        raw = pd.read_csv(os.path.join(HERE, "data", "RELIANCE_5m.csv"), parse_dates=["datetime"]).set_index("datetime")
        day = pd.Timestamp(args.replay)
        for m in range(9 * 60 + 20, 15 * 60 + 41, 5):
            now = day + pd.Timedelta(minutes=m)
            run(raw[raw.index < now], now, dry=True)
        return
    now = ist_now()
    run(fetch_angel(now), now)


if __name__ == "__main__":
    main()
