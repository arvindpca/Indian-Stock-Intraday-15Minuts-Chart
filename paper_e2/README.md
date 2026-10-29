# 🔒 E2 – Sector Pullback (Trend Day) · v1.0 · PAPER (locked 30-Oct-2026)

Paper trading ke dauraan (kam se kam 3 mahine) in rules me koi badlav nahi. Badlav = naya version.

| Step | Rule |
|---|---|
| Stocks | Nifty 500 ke non-F&O, NSE intraday leverage ≥ 4x (roz NSE margin file se), price ≥ ₹50, 20-din median volume ≥ 2 lakh, turnover ≥ ₹5 Cr |
| Din | Somvaar OFF |
| Bada trend | Nifty kal ka close 20-din EMA ke upar → sirf BUY, neeche → sirf SHORT |
| Aaj (10:15) | Nifty kal ke close aur aaj ke open dono ke upar (BUY) / neeche (SHORT), aur bada trend ke saath |
| Sector | Nifty 500 ke liquid stocks ka sector median; Nifty se zyada strong top 2 sector (min 3 stocks) |
| Stock | Us sector me: move +1% se +3.5% (SHORT: -1% se -3.5%), volume (9:15–10:15) ≥ 1.5x pichhle 10 din, VWAP ke sahi side → top 5 |
| Entry | 10:15–13:30, 5-min candle EMA20 ko chhoo kar EMA20 + VWAP ke sahi side green/red close, **Nifty 5-min close EMA20 ke sahi side**, **price aaj ke volume POC ke sahi side**, pullback low/high 1.5% se zyada door nahi. Agli candle (LTP) par entry |
| Size | ₹10,000 margin × stock leverage (4–5x) → ~₹40–50k position. Max 2 trade/din, ek stock ek baar |
| Exit | 3:00 PM, koi fixed SL/target nahi |
| Cost | Angel intraday: ₹20/order brokerage + STT + exchange + SEBI + stamp + GST, slippage 0.03% |

## Backtest (Nov 2024 – Oct 2026, ₹1L capital, ₹10k margin × leverage)
68 trades · +₹13,023 (+13%) · 16/22 mahine profit · max girawat -1.8% · avg/trade +₹192

## Files
- `e2_live.py` — bot · `state/ledger.csv` — har trade · `state/daily.csv` — har din · `state/state.json` — capital
