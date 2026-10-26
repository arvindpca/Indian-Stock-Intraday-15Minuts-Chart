"""Angel One check: sirf login + Reliance 5-min candle fetch. Koi trade/Telegram nahi."""
import os, time, traceback, unicodedata
from datetime import datetime, timedelta, timezone
IST = timezone(timedelta(hours=5, minutes=30))
out = []
for _k in ["ANGEL_API_KEY", "ANGEL_CLIENT_ID", "ANGEL_PASSWORD", "ANGEL_TOTP_SECRET"]:
    os.environ[_k] = "".join(ch for ch in os.getenv(_k, "").strip() if unicodedata.category(ch) != "Cf")
def log(s):
    print(s, flush=True); out.append(str(s))
now = datetime.now(IST)
log(f"time {now:%Y-%m-%d %H:%M} IST")
for k in ["ANGEL_API_KEY", "ANGEL_CLIENT_ID", "ANGEL_PASSWORD", "ANGEL_TOTP_SECRET"]:
    v = os.getenv(k, "")
    log(f"{k}: {'set' if v else 'MISSING'} (len {len(v)})")
try:
    from SmartApi import SmartConnect
    import pyotp
    t0 = time.time()
    api = SmartConnect(api_key=os.environ["ANGEL_API_KEY"])
    login = api.generateSession(os.environ["ANGEL_CLIENT_ID"], os.environ["ANGEL_PASSWORD"],
                                pyotp.TOTP(os.environ["ANGEL_TOTP_SECRET"]).now())
    log(f"login status={login and login.get('status')} message={login and login.get('message')} "
        f"errorcode={login and login.get('errorcode')} ({time.time()-t0:.1f}s)")
    if login and login.get("status"):
        t0 = time.time()
        r = api.getCandleData({"exchange": "NSE", "symboltoken": "2885", "interval": "FIVE_MINUTE",
                               "fromdate": (now - timedelta(days=5)).strftime("%Y-%m-%d 09:15"),
                               "todate": now.strftime("%Y-%m-%d %H:%M")})
        rows = (r or {}).get("data") or []
        log(f"candles status={r and r.get('status')} message={r and r.get('message')} "
            f"errorcode={r and r.get('errorcode')} rows={len(rows)} ({time.time()-t0:.1f}s)")
        if rows:
            log(f"last candle {rows[-1]}")
except Exception:
    log("EXCEPTION:\n" + traceback.format_exc()[-1500:])
os.makedirs("check", exist_ok=True)
open("check/angel_check.txt", "w").write("\n".join(out) + "\n")
