#!/usr/bin/env python3
"""End-of-day paper-trade settlement (runs ~15:40 IST).
Closes any still-open virtual SHORT trades with Angel One 5-minute candles using the
backtest exit rule, then sends the daily paper-trading summary to Telegram."""
import os
import time
from datetime import datetime

import pandas as pd
import requests

import paper_trade as paper
from angel_client import get_client

IST = paper.IST


def send(msg):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN", "").strip(), os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        print(msg)
        return True
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=15,
                      data={"chat_id": chat, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": True})
    print("Telegram:", r.status_code)
    return r.ok


def main():
    client = None

    def candles(trade):
        nonlocal client
        client = client or get_client()
        day, token = trade["date"], trade["token"]
        params = {"exchange": "NSE", "symboltoken": str(token), "interval": "FIVE_MINUTE",
                  "fromdate": f"{day} 09:15", "todate": f"{day} 15:30"}
        r = client.getCandleData(params)
        time.sleep(1.2)
        rows = (r or {}).get("data") or []
        return pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])

    book = paper.PaperBook(send=send)
    closed = book.check_exits(candles, now=datetime.now(IST))
    print(f"settled {closed} open trade(s)")
    df = book.ledger.load()
    today = datetime.now(IST).date()
    if (df["date"] == str(today)).any() or os.getenv("PAPER_ALWAYS_SUMMARY") == "1":
        send(paper.summary_message(df, today))
    else:
        print("No paper trades today - no summary sent.")


if __name__ == "__main__":
    main()
