# Strong Sector Pullback - backtest (sector strength: index)

Stocks: 495 (sector known: 495) | Data: 2023-10-12 -> 2026-10-26
Tradable: AADHARHFC, AARTIIND, ABDL, ABREL, ABSLAMC, ACE, ACMESOLAR, ACUTAAS, AEGISLOG, AEGISVOPAK, AETHER, AFCONS, ANANTRAJ, ANTHEM, ANURAS, APOLLOTYRE, APTUS, ARE&M, ASTERDM, ATGL, AWL, AZAD, BAJAJHFL, BALRAMCHIN, BBOX, BELRISE, BERGEPAINT, BLS, BSOFT, CANHLIFE, CARTRADE, CASTROLIND, CEMPRO, CESC, CGCL, CHAMBLFERT, CHENNPETRO, CHOICEIN, CLEAN, CLEANMAX, CMPDI, COHANCE, CPPLUS, CREDITACC, CUB, CUPID, CYIENT, DATAPATTNS, DEEPAKFERT, DEVYANI, EIDPARRY, ELECON, ELGIEQUIP, EMAMILTD, EMMVEE, ENGINERSIN, EXIDEIND, FINCABLES, FIRSTCRY, FIVESTAR, FRACTAL, FSL, GALLANTT, GESHIP, GICRE, GLAND, GMDCLTD, GPIL, GRANULES, GRAPHITE, GROWW, GRSE, HBLENGINE, HDBFS, HEGAM, HEXT, HINDCOPPER, HONASA, HSCL, HUDCO, ICICIAMC, IDBI, IFCI, IGIL, IGL, IIFL, INDIACEM, INTELLECT, IPCALAB, IRCON, IRCTC, ITCHOTELS, J&KBANK, JAINREC, JINDALSAW, JKTYRE, JMFINANCIL, JSL, JSWCEMENT, JSWINFRA, JUBLINGREA, JWL, JYOTICNC, KAJARIACER, KARURVYSYA, KEC, KIMS, KIRLOSENG, LALPATHLAB, LEMONTREE, LENSKART, LGEINDIA, LLOYDSME, LTFOODS, M&MFIN, MEESHO, MINDACORP, MMTC, MRPL, NATCOPHARM, NAVA, NCC, NETWEB, NIACL, NLCINDIA, NTPCGREEN, NUVAMA, NUVOCO, OLECTRA, ONESOURCE, PARADEEP, PCBL, PFOCUS, PINELABS, PIRAMALFIN, POONAWALLA, PPLPHARMA, PVRINOX, PWL, RAILTEL, REDINGTON, RHIM, RITES, RKFORGE, RRKABEL, SAILIFE, SAMMAANCAP, SANSERA, SARDAEN, SCI, SHYAMMETL, SIGNATURE, SJVN, STARHEALTH, SUMICHEM, SUNTV, SWANCORP, SYNGENE, SYRMA, TARIL, TATACAP, TATACHEM, TATACOMM, TATAINVEST, TATATECH, TDPOWERSYS, TEGA, TEJASNET, TENNIND, TITAGARH, TMCV, TRITURBINE, URBANCO, USHAMART, VAML, WELCORP, WELSPUNLIV, WHIRLPOOL, WOCKPHARMA, ZEEL, ZENSARTECH, ZENTEC
In-sample 2023-10-12 -> 2025-10-17 | Out-of-sample 2025-10-20 -> 2026-10-26
Chosen params (in-sample): {"decision_time": "10:15", "rr": 2.0, "breakeven": true, "top_sectors": 2}

## Ablation (Rs, Rs 1,000 risk per trade, after charges)

| variant                |   IS_trades |   IS_net |   IS_pf |   OOS_trades |   OOS_net |   OOS_win_rate |   OOS_pf |   OOS_avg_R |   OOS_max_dd |   ALL_net |
|:-----------------------|------------:|---------:|--------:|-------------:|----------:|---------------:|---------:|------------:|-------------:|----------:|
| FULL strategy          |          65 |     2176 |    1.07 |           88 |    -17441 |           31.8 |     0.65 |      -0.199 |       -19373 |    -15265 |
| without market filter  |         121 |      694 |    1.01 |          159 |    -35593 |           29.6 |     0.62 |      -0.224 |       -37458 |    -34899 |
| without sector filter  |         245 |   -47826 |    0.67 |          314 |    -48878 |           30.6 |     0.72 |      -0.156 |       -56666 |    -96704 |
| without pullback entry |          82 |   -16756 |    0.59 |          102 |       432 |           51   |     1.01 |       0.005 |        -6591 |    -16324 |

## Best params - by side

|                  |   trades |       net |   win_rate |
|:-----------------|---------:|----------:|-----------:|
| ('IS', 'LONG')   |       37 |  -5442.15 |       0.35 |
| ('IS', 'SHORT')  |       28 |   7617.88 |       0.5  |
| ('OOS', 'LONG')  |       59 | -21018.5  |       0.25 |
| ('OOS', 'SHORT') |       29 |   3577.37 |       0.45 |

## Exit reasons

| period   |   BREAKEVEN |   SL |   TARGET |   TIME |
|:---------|------------:|-----:|---------:|-------:|
| IS       |           7 |   31 |       20 |      7 |
| OOS      |           8 |   48 |       18 |     14 |

## By month

| month   |   count |   sum |
|:--------|--------:|------:|
| 2024-11 |       2 | -1955 |
| 2024-12 |       5 |  -954 |
| 2025-01 |       4 |  4283 |
| 2025-02 |       8 |  2259 |
| 2025-03 |       7 |  -141 |
| 2025-04 |       4 |  1408 |
| 2025-05 |       6 | -1343 |
| 2025-06 |       5 | -2621 |
| 2025-07 |       5 |  -855 |
| 2025-08 |       8 |  1014 |
| 2025-09 |       7 |  4981 |
| 2025-10 |       6 | -3193 |
| 2025-11 |       8 | -2415 |
| 2025-12 |      11 | -7411 |
| 2026-01 |       1 |  1235 |
| 2026-02 |       9 |   815 |
| 2026-03 |       6 |  -873 |
| 2026-04 |       8 | -6022 |
| 2026-05 |      11 | -2406 |
| 2026-06 |       5 |  1405 |
| 2026-07 |      11 |  1755 |
| 2026-08 |       7 | -2970 |
| 2026-09 |       3 |    91 |
| 2026-10 |       6 | -1353 |

## By sector

| sector                            |   count |    sum |
|:----------------------------------|--------:|-------:|
| Financial Services                |      26 | -13787 |
| Information Technology            |      35 |  -6809 |
| Power                             |      10 |  -5994 |
| Media Entertainment & Publication |      11 |  -1995 |
| Healthcare                        |      13 |  -1812 |
| Services                          |       4 |  -1042 |
| Construction                      |       1 |   -882 |
| Metals & Mining                   |      15 |   -121 |
| Oil Gas & Consumable Fuels        |       3 |    564 |
| Construction Materials            |       1 |   1235 |
| Automobile and Auto Components    |      14 |   3536 |
| Realty                            |       7 |   4151 |
| Fast Moving Consumer Goods        |      13 |   7692 |

## Full grid (chosen by IS_pf)

| decision_time   |   rr | breakeven   |   top_sectors |   IS_trades |   IS_days |   IS_net |   IS_win_rate |   IS_pf |   IS_avg_R |   IS_max_dd |   IS_charges |   OOS_trades |   OOS_days |   OOS_net |   OOS_win_rate |   OOS_pf |   OOS_avg_R |   OOS_max_dd |   OOS_charges |
|:----------------|-----:|:------------|--------------:|------------:|----------:|---------:|--------------:|--------:|-----------:|------------:|-------------:|-------------:|-----------:|----------:|---------------:|---------:|------------:|-------------:|--------------:|
| 09:45           |  1.5 | True        |             1 |          37 |        27 |     3712 |          48.6 |    1.23 |      0.1   |       -5554 |         4101 |           44 |         37 |     -5093 |           36.4 |     0.77 |      -0.117 |        -6947 |          4740 |
| 09:45           |  1.5 | True        |             2 |          58 |        40 |    -1600 |          44.8 |    0.95 |     -0.028 |       -7243 |         6392 |           85 |         57 |    -11825 |           35.3 |     0.73 |      -0.14  |       -13429 |          9087 |
| 09:45           |  1.5 | False       |             1 |          37 |        27 |     2723 |          48.6 |    1.16 |      0.073 |       -6543 |         4101 |           44 |         37 |     -1565 |           43.2 |     0.93 |      -0.037 |        -4850 |          4739 |
| 09:45           |  1.5 | False       |             2 |          58 |        40 |    -2589 |          44.8 |    0.91 |     -0.045 |       -8232 |         6392 |           85 |         57 |     -7255 |           40   |     0.83 |      -0.086 |        -8859 |          9087 |
| 09:45           |  2   | True        |             1 |          37 |        27 |     2869 |          40.5 |    1.16 |      0.078 |       -5554 |         4101 |           44 |         37 |     -4173 |           31.8 |     0.82 |      -0.096 |        -8135 |          4739 |
| 09:45           |  2   | True        |             2 |          58 |        40 |    -1627 |          37.9 |    0.95 |     -0.028 |      -10029 |         6392 |           85 |         57 |    -10743 |           30.6 |     0.76 |      -0.127 |       -13342 |          9087 |
| 09:45           |  2   | False       |             1 |          37 |        27 |     2195 |          43.2 |    1.11 |      0.059 |       -6543 |         4101 |           44 |         37 |     -3598 |           36.4 |     0.86 |      -0.083 |        -9294 |          4739 |
| 09:45           |  2   | False       |             2 |          58 |        40 |     -370 |          41.4 |    0.99 |     -0.007 |       -9087 |         6393 |           85 |         57 |     -9785 |           34.1 |     0.8  |      -0.116 |       -12769 |          9087 |
| 10:15           |  1.5 | True        |             1 |          38 |        34 |     4381 |          52.6 |    1.27 |      0.114 |       -3369 |         4216 |           55 |         47 |     -8958 |           36.4 |     0.7  |      -0.164 |       -11593 |          6118 |
| 10:15           |  1.5 | True        |             2 |          65 |        55 |     -944 |          47.7 |    0.97 |     -0.015 |       -6724 |         7159 |           88 |         64 |    -17854 |           35.2 |     0.64 |      -0.204 |       -19221 |          9558 |
| 10:15           |  1.5 | False       |             1 |          38 |        34 |     3392 |          52.6 |    1.19 |      0.088 |       -4249 |         4215 |           55 |         47 |     -6896 |           41.8 |     0.77 |      -0.126 |        -9104 |          6118 |
| 10:15           |  1.5 | False       |             2 |          65 |        55 |    -3285 |          47.7 |    0.9  |     -0.051 |       -6724 |         7159 |           88 |         64 |    -14298 |           39.8 |     0.71 |      -0.163 |       -15665 |          9558 |
| 10:15           |  2   | True        |             1 |          38 |        34 |     6691 |          44.7 |    1.39 |      0.175 |       -4665 |         4216 |           55 |         47 |     -7594 |           34.5 |     0.75 |      -0.139 |       -10817 |          6118 |
| 10:15           |  2   | True        |             2 |          65 |        55 |     2176 |          41.5 |    1.07 |      0.033 |       -7400 |         7159 |           88 |         64 |    -17441 |           31.8 |     0.65 |      -0.199 |       -19373 |          9558 |
| 10:15           |  2   | False       |             1 |          38 |        34 |     6830 |          50   |    1.37 |      0.179 |       -4304 |         4216 |           55 |         47 |     -5702 |           40   |     0.82 |      -0.104 |        -8498 |          6118 |
| 10:15           |  2   | False       |             2 |          65 |        55 |      -36 |          44.6 |    1    |     -0.001 |       -7784 |         7159 |           88 |         64 |    -10678 |           38.6 |     0.79 |      -0.122 |       -12544 |          9558 |