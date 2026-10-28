# Strong Sector Pullback - backtest (sector strength: median)

Stocks: 495 (sector known: 495) | Data: 2023-10-12 -> 2026-10-26
Tradable: AADHARHFC, AARTIIND, ABDL, ABREL, ABSLAMC, ACE, ACMESOLAR, ACUTAAS, AEGISLOG, AEGISVOPAK, AETHER, AFCONS, ANANTRAJ, ANTHEM, ANURAS, APOLLOTYRE, APTUS, ARE&M, ASTERDM, ATGL, AWL, AZAD, BAJAJHFL, BALRAMCHIN, BBOX, BELRISE, BERGEPAINT, BLS, BSOFT, CANHLIFE, CARTRADE, CASTROLIND, CEMPRO, CESC, CGCL, CHAMBLFERT, CHENNPETRO, CHOICEIN, CLEAN, CLEANMAX, CMPDI, COHANCE, CPPLUS, CREDITACC, CUB, CUPID, CYIENT, DATAPATTNS, DEEPAKFERT, DEVYANI, EIDPARRY, ELECON, ELGIEQUIP, EMAMILTD, EMMVEE, ENGINERSIN, EXIDEIND, FINCABLES, FIRSTCRY, FIVESTAR, FRACTAL, FSL, GALLANTT, GESHIP, GICRE, GLAND, GMDCLTD, GPIL, GRANULES, GRAPHITE, GROWW, GRSE, HBLENGINE, HDBFS, HEGAM, HEXT, HINDCOPPER, HONASA, HSCL, HUDCO, ICICIAMC, IDBI, IFCI, IGIL, IGL, IIFL, INDIACEM, INTELLECT, IPCALAB, IRCON, IRCTC, ITCHOTELS, J&KBANK, JAINREC, JINDALSAW, JKTYRE, JMFINANCIL, JSL, JSWCEMENT, JSWINFRA, JUBLINGREA, JWL, JYOTICNC, KAJARIACER, KARURVYSYA, KEC, KIMS, KIRLOSENG, LALPATHLAB, LEMONTREE, LENSKART, LGEINDIA, LLOYDSME, LTFOODS, M&MFIN, MEESHO, MINDACORP, MMTC, MRPL, NATCOPHARM, NAVA, NCC, NETWEB, NIACL, NLCINDIA, NTPCGREEN, NUVAMA, NUVOCO, OLECTRA, ONESOURCE, PARADEEP, PCBL, PFOCUS, PINELABS, PIRAMALFIN, POONAWALLA, PPLPHARMA, PVRINOX, PWL, RAILTEL, REDINGTON, RHIM, RITES, RKFORGE, RRKABEL, SAILIFE, SAMMAANCAP, SANSERA, SARDAEN, SCI, SHYAMMETL, SIGNATURE, SJVN, STARHEALTH, SUMICHEM, SUNTV, SWANCORP, SYNGENE, SYRMA, TARIL, TATACAP, TATACHEM, TATACOMM, TATAINVEST, TATATECH, TDPOWERSYS, TEGA, TEJASNET, TENNIND, TITAGARH, TMCV, TRITURBINE, URBANCO, USHAMART, VAML, WELCORP, WELSPUNLIV, WHIRLPOOL, WOCKPHARMA, ZEEL, ZENSARTECH, ZENTEC
In-sample 2023-10-12 -> 2025-10-17 | Out-of-sample 2025-10-20 -> 2026-10-26
Chosen params (in-sample): {"decision_time": "10:15", "rr": 2.0, "breakeven": false, "top_sectors": 2}

## Ablation (Rs, Rs 1,000 risk per trade, after charges)

| variant                |   IS_trades |   IS_net |   IS_pf |   OOS_trades |   OOS_net |   OOS_win_rate |   OOS_pf |   OOS_avg_R |   OOS_max_dd |   ALL_net |
|:-----------------------|------------:|---------:|--------:|-------------:|----------:|---------------:|---------:|------------:|-------------:|----------:|
| FULL strategy          |          66 |    -5812 |    0.85 |           80 |     -7355 |           37.5 |     0.84 |      -0.093 |       -12835 |    -13167 |
| without market filter  |         126 |   -30406 |    0.63 |          150 |    -25696 |           35.3 |     0.72 |      -0.172 |       -28765 |    -56103 |
| without sector filter  |         246 |   -49967 |    0.68 |          315 |    -46525 |           35.2 |     0.75 |      -0.148 |       -52401 |    -96491 |
| without pullback entry |          86 |   -12722 |    0.7  |           95 |     -1640 |           48.4 |     0.96 |      -0.017 |        -8076 |    -14363 |

## Best params - by side

|                  |   trades |       net |   win_rate |
|:-----------------|---------:|----------:|-----------:|
| ('IS', 'LONG')   |       39 | -11031.8  |       0.31 |
| ('IS', 'SHORT')  |       27 |   5219.49 |       0.52 |
| ('OOS', 'LONG')  |       50 | -14679.8  |       0.28 |
| ('OOS', 'SHORT') |       30 |   7324.92 |       0.53 |

## Exit reasons

| period   |   SL |   TARGET |   TIME |
|:---------|-----:|---------:|-------:|
| IS       |   39 |       20 |      7 |
| OOS      |   45 |       20 |     15 |

## By month

| month   |   count |   sum |
|:--------|--------:|------:|
| 2024-11 |       5 | -2219 |
| 2024-12 |       8 | -3013 |
| 2025-01 |       7 |  1668 |
| 2025-02 |       7 | -1418 |
| 2025-03 |       4 | -1397 |
| 2025-04 |       6 |  3700 |
| 2025-05 |       5 |  2117 |
| 2025-06 |       2 | -1713 |
| 2025-07 |       8 | -1508 |
| 2025-08 |       2 |   496 |
| 2025-09 |       8 |  1374 |
| 2025-10 |       8 | -3421 |
| 2025-11 |       8 | -2409 |
| 2025-12 |      10 | -3128 |
| 2026-01 |       3 |   991 |
| 2026-02 |       9 | -2946 |
| 2026-03 |       3 | -3149 |
| 2026-04 |       4 |  -859 |
| 2026-05 |       3 |  2892 |
| 2026-06 |       6 |  -827 |
| 2026-07 |       8 |  1027 |
| 2026-08 |      11 | -1121 |
| 2026-09 |       2 |  2278 |
| 2026-10 |       9 |  -582 |

## By sector

| sector                            |   count |   sum |
|:----------------------------------|--------:|------:|
| Media Entertainment & Publication |      14 | -6221 |
| Financial Services                |       9 | -5945 |
| Metals & Mining                   |      11 | -4023 |
| Consumer Services                 |       6 | -3068 |
| Power                             |       5 | -2746 |
| Oil Gas & Consumable Fuels        |       2 | -1711 |
| Capital Goods                     |      11 | -1580 |
| Automobile and Auto Components    |       4 | -1287 |
| Information Technology            |      31 |  -823 |
| Chemicals                         |       4 |  -270 |
| Construction                      |      11 |   -37 |
| Consumer Durables                 |       2 |     2 |
| Textiles                          |       4 |   736 |
| Construction Materials            |       4 |  1371 |
| Services                          |       3 |  1421 |
| Fast Moving Consumer Goods        |       5 |  2041 |
| Healthcare                        |       7 |  2418 |
| Telecommunication                 |       7 |  3136 |
| Realty                            |       6 |  3418 |

## Full grid (chosen by IS_pf)

| decision_time   |   rr | breakeven   |   top_sectors |   IS_trades |   IS_days |   IS_net |   IS_win_rate |   IS_pf |   IS_avg_R |   IS_max_dd |   IS_charges |   OOS_trades |   OOS_days |   OOS_net |   OOS_win_rate |   OOS_pf |   OOS_avg_R |   OOS_max_dd |   OOS_charges |
|:----------------|-----:|:------------|--------------:|------------:|----------:|---------:|--------------:|--------:|-----------:|------------:|-------------:|-------------:|-----------:|----------:|---------------:|---------:|------------:|-------------:|--------------:|
| 09:45           |  1.5 | True        |             1 |          22 |        18 |     1404 |          50   |    1.15 |      0.063 |       -4588 |         2432 |           45 |         37 |     -4740 |           35.6 |     0.78 |      -0.106 |        -9062 |          4765 |
| 09:45           |  1.5 | True        |             2 |          48 |        33 |     5329 |          52.1 |    1.25 |      0.11  |       -6468 |         5111 |           76 |         55 |    -12673 |           34.2 |     0.68 |      -0.167 |       -14451 |          8178 |
| 09:45           |  1.5 | False       |             1 |          22 |        18 |      406 |          50   |    1.04 |      0.018 |       -4588 |         2431 |           45 |         37 |     -3201 |           42.2 |     0.86 |      -0.072 |        -8563 |          4765 |
| 09:45           |  1.5 | False       |             2 |          48 |        33 |     4332 |          52.1 |    1.2  |      0.09  |       -6468 |         5111 |           76 |         55 |     -9968 |           39.5 |     0.76 |      -0.132 |       -12713 |          8178 |
| 09:45           |  2   | True        |             1 |          22 |        18 |     1606 |          45.5 |    1.15 |      0.073 |       -4588 |         2431 |           45 |         37 |     -2261 |           33.3 |     0.9  |      -0.051 |        -7734 |          4765 |
| 09:45           |  2   | True        |             2 |          48 |        33 |     7979 |          45.8 |    1.36 |      0.166 |       -6532 |         5112 |           76 |         55 |    -15131 |           26.3 |     0.63 |      -0.2   |       -14991 |          8178 |
| 09:45           |  2   | False       |             1 |          22 |        18 |      608 |          45.5 |    1.05 |      0.028 |       -4588 |         2431 |           45 |         37 |     -1981 |           37.8 |     0.92 |      -0.045 |        -6959 |          4765 |
| 09:45           |  2   | False       |             2 |          48 |        33 |     8682 |          50   |    1.38 |      0.181 |       -6532 |         5112 |           76 |         55 |    -14612 |           31.6 |     0.68 |      -0.193 |       -13766 |          8178 |
| 10:15           |  1.5 | True        |             1 |          31 |        27 |      949 |          51.6 |    1.06 |      0.03  |       -5246 |         3402 |           42 |         38 |     -1767 |           42.9 |     0.92 |      -0.043 |        -9081 |          4581 |
| 10:15           |  1.5 | True        |             2 |          66 |        48 |    -5884 |          42.4 |    0.83 |     -0.09  |       -7234 |         7192 |           80 |         64 |    -10732 |           38.8 |     0.75 |      -0.135 |       -14865 |          8776 |
| 10:15           |  1.5 | False       |             1 |          31 |        27 |      949 |          51.6 |    1.06 |      0.03  |       -5246 |         3402 |           42 |         38 |       730 |           47.6 |     1.04 |       0.016 |        -7582 |          4581 |
| 10:15           |  1.5 | False       |             2 |          66 |        48 |    -5890 |          43.9 |    0.84 |     -0.09  |       -8165 |         7192 |           80 |         64 |     -9060 |           41.2 |     0.79 |      -0.114 |       -13366 |          8776 |
| 10:15           |  2   | True        |             1 |          31 |        27 |     -203 |          41.9 |    0.99 |     -0.007 |       -6597 |         3402 |           42 |         38 |     -1704 |           38.1 |     0.92 |      -0.041 |        -9714 |          4581 |
| 10:15           |  2   | True        |             2 |          66 |        48 |    -5841 |          34.8 |    0.84 |     -0.089 |       -7926 |         7192 |           80 |         64 |    -10013 |           33.8 |     0.78 |      -0.126 |       -14173 |          8776 |
| 10:15           |  2   | False       |             1 |          31 |        27 |      925 |          48.4 |    1.06 |      0.03  |       -5856 |         3402 |           42 |         38 |      1778 |           45.2 |     1.08 |       0.042 |        -8376 |          4581 |
| 10:15           |  2   | False       |             2 |          66 |        48 |    -5812 |          39.4 |    0.85 |     -0.088 |       -9239 |         7193 |           80 |         64 |     -7355 |           37.5 |     0.84 |      -0.093 |       -12835 |          8775 |