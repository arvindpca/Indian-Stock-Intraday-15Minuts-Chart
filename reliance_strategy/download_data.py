"""Angel One se Reliance + Nifty ka 1 saal ka 5-min aur 15-min data download karke CSV me save karta hai."""
import os
import sys
import time
import unicodedata
from datetime import datetime, timedelta

import pandas as pd

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SYMBOLS = {"RELIANCE": ("NSE", "2885"), "NIFTY": ("NSE", "99926000")}
INTERVALS = {"FIFTEEN_MINUTE": ("15m", 90), "FIVE_MINUTE": ("5m", 30)}


def _env(name):
    v = os.getenv(name, "")
    return "".join(ch for ch in v.strip() if unicodedata.category(ch) != "Cf")


def main(days=380):
    from SmartApi import SmartConnect
    import pyotp

    api = SmartConnect(api_key=_env("ANGEL_API_KEY"))
    login = api.generateSession(_env("ANGEL_CLIENT_ID"), _env("ANGEL_PASSWORD"),
                                pyotp.TOTP(_env("ANGEL_TOTP_SECRET")).now())
    if not login or not login.get("status"):
        sys.exit(f"Angel One login fail: {login and login.get('message')}")
    os.makedirs(OUT, exist_ok=True)
    end = datetime.now()
    for sym, (exch, token) in SYMBOLS.items():
        for iv, (tag, chunk) in INTERVALS.items():
            rows, cur = [], end - timedelta(days=days)
            while cur < end:
                nxt = min(cur + timedelta(days=chunk), end)
                for attempt in range(3):
                    try:
                        r = api.getCandleData({"exchange": exch, "symboltoken": token, "interval": iv,
                                               "fromdate": cur.strftime("%Y-%m-%d 09:15"),
                                               "todate": nxt.strftime("%Y-%m-%d 15:30")})
                        rows += (r or {}).get("data") or []
                        break
                    except Exception as e:
                        print("retry", sym, tag, cur.date(), e)
                        time.sleep(3)
                cur = nxt + timedelta(days=1)
                time.sleep(0.5)
            df = pd.DataFrame(rows, columns=["datetime", "open", "high", "low", "close", "volume"])
            df["datetime"] = pd.to_datetime(df["datetime"]).dt.tz_localize(None)
            df = df.drop_duplicates("datetime").sort_values("datetime")
            path = os.path.join(OUT, f"{sym}_{tag}.csv")
            df.to_csv(path, index=False)
            print(f"{sym} {tag}: {len(df)} candles -> {path}")


if __name__ == "__main__":
    main()
