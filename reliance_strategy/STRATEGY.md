# Reliance Intraday — Final Strategy (Reverse Engineering se)

**Naam:** 20-Minute Momentum + 2-ATR Trailing Stop
**Chart:** Reliance 5-minute | **Data:** Angel One, 10 Oct 2025 – 23 Oct 2026 (256 din)

## Rules

1. 5-min chart pe **ATR(14)** lagao.
2. **Move** = (abhi ka close − 4 candle pehle ka close) ÷ ATR
   - Move **+1.5 se upar** → **BUY**
   - Move **−1.5 se neeche** → **SELL**
3. Signal wali candle band hone ke baad, **agli candle ke open** pe entry.
4. Entry sirf **9:30 – 14:30**. **Din me sirf 1 trade.**
5. **Stop loss = 2 × ATR**, aur har candle pe trail karo (BUY: close − 2×ATR, SELL: close + 2×ATR). SL sirf aapke favour me khiskega.
6. **Koi fixed target nahi.** 15:15 pe jo bacha, band.

Example: ATR = 3 rupaye. 20 min me Reliance 1200 se 1205 gaya → move = 5/3 = 1.67 → BUY agli candle pe ~1205. SL = 1199. Price 1212 gaya to SL 1206 ho jayega (close − 6).

## Result (charges 0.06% kaat ke)

| | Trades | Win % | Avg win | Avg loss | Profit Factor | Total | Max DD | Profit wale mahine |
|---|---|---|---|---|---|---|---|---|
| Pura saal | 254 | 39% | +0.77% | −0.38% | **1.28** | +16.8% | −6.3% | 10/13 |
| Pehle 8 mahine (rule yahan se chuna) | 168 | 39% | +0.76% | −0.38% | 1.29 | +11.2% | −6.3% | 6/9 |
| Aakhri 4 mahine (rule ne dekha hi nahi) | 86 | 38% | +0.80% | −0.39% | **1.27** | +5.5% | −4.0% | 4/5 |
| Sirf BUY | 122 | 37% | | | 1.04 | +1.1% | | |
| Sirf SELL | 132 | 41% | | | 1.55 | +15.6% | | |

"Total %" = har trade ka % return jod ke (ek trade pe pura capital, bina leverage).

## Kaise mila (reverse engineering)

1. Pehle 8 mahine ke har 5-min candle ke baad dekha: kya agle 2 ghante me 0.6% move aaya? Uske pehle 20 cheezein (time, gap, VWAP, RSI, volume, Nifty, etc.) compare ki.
   → Girte din + high volume pe short sabse achha dikha (38–43%)… **par aakhri 4 mahine me 4–15% pe gir gaya.** Ye us samay ka trend tha, pattern nahi.
2. Market ka "swabhav" check kiya: sirf ek cheez dono hisso me same thi — **1 ghante ke scale pe halka momentum.**
3. 644 rule combinations test kiye. Train me achhe dikhne walo me se sirf ~aadhe test me chale → zyada tar kismat.
4. Momentum + trailing stop family alag se check ki: **128 alag settings me se 88% train me aur 70% test me profit me.** Ek lucky number pe nahi tika — isliye beech wali safe setting chuni, sabse top wali nahi.
5. Extra filter (Nifty direction, volume, VWAP, EMA) se koi pakka sudhaar nahi mila → simple rakha.

## Zaroori sachchai (risk)

- **Edge chhota hai.** Profit Factor 1.28 matlab ₹100 gawaye to ₹128 kamaye. 61% trade loss me jaate hain; lagatar **9 loss** tak aaye hain.
- **Profit ka zyada hissa SELL se aaya**, aur Reliance is saal 1381 → 1190 gira. Upar jaate saal me BUY side kamzor (PF 1.04) hai.
- **Yahi rule Nifty pe fail hua** (PF 0.63). Ye Reliance-specific hai.
- Charges 0.10% maan lo to PF 1.10 tak gir jaata hai — **sasta brokerage zaroori.**
- Ye sirf 1 saal ka data hai. **Live paise lagane se pehle kam se kam 1–2 mahine paper trade karo.**

## Files

- `final_strategy.py` — backtest + `--signal-today` (last candle ka signal)
- `download_data.py` — Angel One se data (workflow: *Reliance Data Download*)
- `results/final_strategy_trades.csv` — saare 254 trades
