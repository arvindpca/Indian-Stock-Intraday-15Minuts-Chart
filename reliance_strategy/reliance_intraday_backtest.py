"""
Reliance Intraday Strategy Finder
---------------------------------
1 saal ka 15-min data leke 5 common indicator strategies ko intraday test karta hai
aur sabse achhi strategy batata hai.

Data 2 tarike se:
  A) Angel One SmartAPI se (env vars set karo):
       ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_PASSWORD (MPIN), ANGEL_TOTP_SECRET
       pip install smartapi-python pyotp pandas numpy
       python reliance_intraday_backtest.py
  B) CSV se (TradingView / broker export, columns: datetime,open,high,low,close,volume):
       python reliance_intraday_backtest.py --csv RELIANCE_15m.csv

Rules (sab strategies ke liye same):
  - Entry sirf 9:30 se 14:30 ke beech
  - 15:15 pe har position band (pure intraday)
  - Stop loss = 1 x ATR(14), Target = 2 x ATR(14)
  - Ek din me max 2 trade
  - Charges + slippage = 0.06% per round trip (approx)
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

COST = 0.0006          # 0.06% round trip
SL_ATR, TP_ATR = 1.0, 2.0
MAX_TRADES_PER_DAY = 2


# ----------------------------------------------------------------- data
def _env(name):
    import unicodedata
    v = os.getenv(name, "")
    return "".join(ch for ch in v.strip() if unicodedata.category(ch) != "Cf")


def load_angel(days=365):
    import time
    from SmartApi import SmartConnect
    import pyotp

    api = SmartConnect(api_key=_env("ANGEL_API_KEY"))
    login = api.generateSession(_env("ANGEL_CLIENT_ID"), _env("ANGEL_PASSWORD"),
                                pyotp.TOTP(_env("ANGEL_TOTP_SECRET")).now())
    if not login or not login.get("status"):
        sys.exit(f"Angel One login fail: {login and login.get('message')}")
    token = "2885"  # RELIANCE-EQ NSE token
    end = datetime.now()
    start = end - timedelta(days=days)
    rows = []
    cur = start
    while cur < end:  # 15-min data ek request me ~100 din tak
        nxt = min(cur + timedelta(days=90), end)
        r = api.getCandleData({
            "exchange": "NSE", "symboltoken": token, "interval": "FIFTEEN_MINUTE",
            "fromdate": cur.strftime("%Y-%m-%d 09:15"), "todate": nxt.strftime("%Y-%m-%d 15:30"),
        })
        rows += (r or {}).get("data") or []
        cur = nxt + timedelta(days=1)
        time.sleep(1)  # rate limit
    df = pd.DataFrame(rows, columns=["datetime", "open", "high", "low", "close", "volume"])
    df["datetime"] = pd.to_datetime(df["datetime"]).dt.tz_localize(None)
    return df.drop_duplicates("datetime").set_index("datetime").sort_index()


def load_csv(path):
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    tcol = next(c for c in df.columns if c in ("datetime", "date", "time", "timestamp"))
    df["datetime"] = pd.to_datetime(df[tcol])
    if df["datetime"].dt.tz is not None:
        df["datetime"] = df["datetime"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    return df.set_index("datetime")[["open", "high", "low", "close", "volume"]].sort_index()


# ----------------------------------------------------------- indicators
def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def atr(df, n=14):
    pc = df.close.shift()
    tr = pd.concat([df.high - df.low, (df.high - pc).abs(), (df.low - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def supertrend(df, n=10, mult=3):
    a = atr(df, n)
    hl2 = (df.high + df.low) / 2
    ub, lb = (hl2 + mult * a).values, (hl2 - mult * a).values
    c = df.close.values
    fu, fl, d = ub.copy(), lb.copy(), np.ones(len(df))
    for i in range(1, len(df)):
        fu[i] = ub[i] if (ub[i] < fu[i - 1] or c[i - 1] > fu[i - 1]) else fu[i - 1]
        fl[i] = lb[i] if (lb[i] > fl[i - 1] or c[i - 1] < fl[i - 1]) else fl[i - 1]
        d[i] = 1 if c[i] > fu[i - 1] else (-1 if c[i] < fl[i - 1] else d[i - 1])
    return pd.Series(d, index=df.index)


def add_indicators(df):
    df = df.copy()
    df["day"] = df.index.date
    df["ema9"], df["ema21"] = ema(df.close, 9), ema(df.close, 21)
    df["ema50"] = ema(df.close, 50)
    df["rsi"] = rsi(df.close)
    df["atr"] = atr(df)
    m = ema(df.close, 12) - ema(df.close, 26)
    df["macd"], df["macds"] = m, ema(m, 9)
    tp = (df.high + df.low + df.close) / 3
    df["vwap"] = (tp * df.volume).groupby(df.day).cumsum() / df.volume.groupby(df.day).cumsum()
    df["st"] = supertrend(df)
    df["volavg"] = df.volume.rolling(20).mean()
    first = df.groupby("day").head(1)  # 9:15 wali pehli 15-min candle = opening range
    df["orh"] = df.day.map(dict(zip(first.day, first.high)))
    df["orl"] = df.day.map(dict(zip(first.day, first.low)))
    return df


# ------------------------------------------------------------ strategies
# har function +1 (buy), -1 (sell), 0 (kuch nahi) deta hai, candle close pe
def s_ema_cross(df):
    up = (df.ema9 > df.ema21) & (df.ema9.shift() <= df.ema21.shift())
    dn = (df.ema9 < df.ema21) & (df.ema9.shift() >= df.ema21.shift())
    return np.where(up, 1, np.where(dn, -1, 0))


def s_vwap_rsi(df):
    up = (df.close > df.vwap) & (df.close.shift() <= df.vwap.shift()) & (df.rsi > 50)
    dn = (df.close < df.vwap) & (df.close.shift() >= df.vwap.shift()) & (df.rsi < 50)
    return np.where(up, 1, np.where(dn, -1, 0))


def s_supertrend(df):
    flip = df.st != df.st.shift()
    return np.where(flip & (df.st == 1), 1, np.where(flip & (df.st == -1), -1, 0))


def s_macd_ema(df):
    up = (df.macd > df.macds) & (df.macd.shift() <= df.macds.shift()) & (df.close > df.ema50)
    dn = (df.macd < df.macds) & (df.macd.shift() >= df.macds.shift()) & (df.close < df.ema50)
    return np.where(up, 1, np.where(dn, -1, 0))


def s_orb(df):
    up = (df.close > df.orh) & (df.close.shift() <= df.orh) & (df.volume > df.volavg)
    dn = (df.close < df.orl) & (df.close.shift() >= df.orl) & (df.volume > df.volavg)
    return np.where(up, 1, np.where(dn, -1, 0))


def s_combo(df):
    """Sab common indicator ek saath: VWAP + EMA trend + RSI + Supertrend direction."""
    trend_up = (df.close > df.vwap) & (df.ema9 > df.ema21) & (df.st == 1)
    trend_dn = (df.close < df.vwap) & (df.ema9 < df.ema21) & (df.st == -1)
    up = trend_up & (df.rsi > 55) & ~(trend_up.shift(fill_value=False) & (df.rsi.shift() > 55))
    dn = trend_dn & (df.rsi < 45) & ~(trend_dn.shift(fill_value=False) & (df.rsi.shift() < 45))
    return np.where(up, 1, np.where(dn, -1, 0))


STRATEGIES = {
    "EMA 9/21 Crossover": s_ema_cross,
    "VWAP Cross + RSI 50": s_vwap_rsi,
    "Supertrend (10,3)": s_supertrend,
    "MACD + EMA50 filter": s_macd_ema,
    "Opening Range Breakout": s_orb,
    "Combo (VWAP+EMA+RSI+ST)": s_combo,
}


# -------------------------------------------------------------- backtest
def backtest(df, sig):
    t = df.index.time
    o, h, l, c, a = df.open.values, df.high.values, df.low.values, df.close.values, df.atr.values
    days = df.day.values
    t930, t1430, t1515 = pd.Timestamp("09:30").time(), pd.Timestamp("14:30").time(), pd.Timestamp("15:15").time()
    trades, pos, cnt, cur_day = [], 0, 0, None
    for i in range(len(df) - 1):
        if days[i] != cur_day:
            cur_day, cnt = days[i], 0
        if pos:
            j = i  # current bar ke andar SL/TP check
            hit = None
            if pos == 1:
                if l[j] <= sl: hit = sl
                elif h[j] >= tp: hit = tp
            else:
                if h[j] >= sl: hit = sl
                elif l[j] <= tp: hit = tp
            if hit is None and (t[j] >= t1515 or days[j + 1] != days[j]):
                hit = c[j]
            if hit is not None:
                trades.append((df.index[i], pos * (hit - entry) / entry - COST))
                pos = 0
                continue
        if not pos and sig[i] != 0 and t930 <= t[i] <= t1430 and cnt < MAX_TRADES_PER_DAY \
                and days[i + 1] == days[i] and not np.isnan(a[i]):
            pos = int(sig[i])
            entry = o[i + 1]  # agli candle ke open pe entry (realistic)
            sl = entry - pos * SL_ATR * a[i]
            tp = entry + pos * TP_ATR * a[i]
            cnt += 1
    return pd.DataFrame(trades, columns=["time", "ret"])


def stats(tr):
    if tr.empty:
        return dict(Trades=0)
    r = tr.ret
    eq = (1 + r).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    wins, loss = r[r > 0].sum(), -r[r < 0].sum()
    return {
        "Trades": len(r),
        "Win %": round(100 * (r > 0).mean(), 1),
        "Total Return %": round(100 * (eq.iloc[-1] - 1), 1),
        "Avg/Trade %": round(100 * r.mean(), 3),
        "Profit Factor": round(wins / loss, 2) if loss else np.inf,
        "Max DD %": round(100 * dd, 1),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv")
    args = ap.parse_args()
    df = load_csv(args.csv) if args.csv else load_angel()
    df = df.between_time("09:15", "15:29")
    print(f"Data: {len(df)} candles | {df.index[0].date()} se {df.index[-1].date()} tak\n")
    df = add_indicators(df)

    # pehle 8 mahine pe test (in-sample), aakhri 4 mahine pe check (out-of-sample)
    split = df.index[0] + (df.index[-1] - df.index[0]) * 2 / 3
    rows = []
    for name, fn in STRATEGIES.items():
        tr = backtest(df, fn(df))
        full = stats(tr)
        oos = stats(tr[tr.time >= split])
        full["Last 4M Return %"] = oos.get("Total Return %", 0)
        rows.append({"Strategy": name, **full})
    res = pd.DataFrame(rows).set_index("Strategy").sort_values("Profit Factor", ascending=False)
    pd.set_option("display.width", 200)
    print(res.to_string())
    best = res.index[0]
    print(f"\nSabse achhi (Profit Factor ke hisaab se): {best}")
    if res.loc[best, "Last 4M Return %"] <= 0:
        print("Dhyan do: aakhri 4 mahine me ye bhi loss me hai -> edge kamzor hai, live se pehle paper trade karo.")
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
    os.makedirs(out, exist_ok=True)
    res.to_csv(os.path.join(out, "reliance_strategy_results.csv"))
    with open(os.path.join(out, "summary.md"), "w") as f:
        f.write(f"## Reliance intraday strategy test ({df.index[0].date()} to {df.index[-1].date()})\n\n")
        f.write(res.to_markdown() + f"\n\n**Best (Profit Factor):** {best}\n")
    send_telegram(res, best)
    print("Result saved:", out)


def send_telegram(res, best):
    tok, chat = _env("TELEGRAM_BOT_TOKEN"), _env("TELEGRAM_CHAT_ID")
    if not (tok and chat):
        return
    import requests
    lines = [f"Reliance 1Y intraday test - Best: {best}"]
    for n, r in res.iterrows():
        lines.append(f"{n}: PF {r['Profit Factor']}, Win {r['Win %']}%, Ret {r['Total Return %']}%, Last4M {r['Last 4M Return %']}%")
    try:
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      data={"chat_id": chat, "text": "\n".join(lines)}, timeout=15)
    except Exception as e:
        print("Telegram fail:", e)


if __name__ == "__main__":
    main()
