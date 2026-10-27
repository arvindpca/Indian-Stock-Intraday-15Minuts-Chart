# Strong Sector Pullback - backtest

Stocks: 253 (sector known: 253) | Data: 2023-10-12 -> 2026-10-26
Tradable: AADHARHFC, AARTIIND, AAVAS, ABBOTINDIA, ABDL, ABREL, ABSLAMC, ACC, ACE, ACMESOLAR, ACUTAAS, AEGISLOG, AEGISVOPAK, AETHER, AFCONS, AFFLE, AIAENG, AIIL, AJANTPHARM, ANANTRAJ, ANTHEM, ANURAS, APARINDS, APOLLOTYRE, APTUS, ARE&M, ASAHIINDIA, ASTERDM, ATGL, ATUL, AVANTIFEED, AWL, AZAD, BAJAJHFL, BALKRISIND, BALRAMCHIN, BATAINDIA, BBOX, BELRISE, BEML, BERGEPAINT, BHARATCOAL, BHARTIHEXA, BLS, BRIGADE, BSOFT, CANFINHOME, CANHLIFE, CAPLIPOINT, CARBORUNIV, CARTRADE, CASTROLIND, CCL, CEATLTD, CEMPRO, CENTRALBK, CESC, CGCL, CHAMBLFERT, CHENNPETRO, CHOICEIN, CHOLAHLDNG, CIEINDIA, CLEAN, CLEANMAX, CMPDI, COHANCE, CONCORDBIO, COROMANDEL, CPPLUS, CRAFTSMAN, CREDITACC, CRISIL, CUB, CUPID, CYIENT, DALBHARAT, EIHOTEL, ELGIEQUIP, EMAMILTD, EMCURE, EMMVEE, ENGINERSIN, ESCORTS, FSL, GALLANTT, GICRE, GLAND, GMDCLTD, GODREJIND, GRSE, HDBFS, HINDCOPPER, HOMEFIRST, HSCL, IIFL, INOXINDIA, JAINREC, JKTYRE, JSL, JSWINFRA, KAJARIACER, KEC, KSB, LGEINDIA, MGL, MINDACORP, NH, NLCINDIA, NUVAMA, NUVOCO, OLECTRA, PCBL, PIRAMALFIN, PVRINOX, RAINBOW, REDINGTON, RHIM, RKFORGE, RRKABEL, SAILIFE, SARDAEN, SCHNEIDER, SCI, SHYAMMETL, STARHEALTH, SUMICHEM, SYNGENE, TATACAP, TATACHEM, TATACOMM, TATAINVEST, TDPOWERSYS, TECHNOE, TEGA, TEJASNET, TIMKEN, TMCV, TRITURBINE, USHAMART, VAML, WELCORP, WELSPUNLIV, WHIRLPOOL, ZENSARTECH, ZENTEC, ZFCVINDIA
In-sample 2023-10-12 -> 2025-10-17 | Out-of-sample 2025-10-20 -> 2026-10-26
Chosen params (in-sample): {"decision_time": "10:15", "rr": 2.0, "breakeven": true, "top_sectors": 1}

## Ablation (Rs, Rs 1,000 risk per trade, after charges)

| variant                |   IS_trades |   IS_net |   IS_pf |   OOS_trades |   OOS_net |   OOS_win_rate |   OOS_pf |   OOS_avg_R |   OOS_max_dd |   ALL_net |
|:-----------------------|------------:|---------:|--------:|-------------:|----------:|---------------:|---------:|------------:|-------------:|----------:|
| FULL strategy          |          30 |    10112 |    1.92 |           36 |    -10287 |           25   |     0.54 |      -0.286 |       -11270 |      -176 |
| without market filter  |          59 |    -8251 |    0.76 |           62 |     -8208 |           29   |     0.75 |      -0.133 |       -10852 |    -16458 |
| without sector filter  |         205 |   -46500 |    0.63 |          283 |    -49444 |           31.4 |     0.68 |      -0.175 |       -52527 |    -95944 |
| without pullback entry |          42 |    -1632 |    0.91 |           43 |     -4498 |           44.2 |     0.76 |      -0.105 |        -7974 |     -6131 |

## Best params - by side

|                  |   trades |      net |   win_rate |
|:-----------------|---------:|---------:|-----------:|
| ('IS', 'LONG')   |       19 |  3616.13 |       0.47 |
| ('IS', 'SHORT')  |       11 |  6495.73 |       0.73 |
| ('OOS', 'LONG')  |       19 | -7505.92 |       0.26 |
| ('OOS', 'SHORT') |       17 | -2781.53 |       0.24 |

## Exit reasons

| period   |   BREAKEVEN |   SL |   TARGET |   TIME |
|:---------|------------:|-----:|---------:|-------:|
| IS       |           2 |   11 |       14 |      3 |
| OOS      |           5 |   22 |        6 |      3 |

## By month

| month   |   count |   sum |
|:--------|--------:|------:|
| 2024-12 |       2 |  -364 |
| 2025-01 |       2 |  1165 |
| 2025-02 |       2 |   722 |
| 2025-03 |       3 |  -599 |
| 2025-04 |       4 |  3099 |
| 2025-05 |       5 |  5376 |
| 2025-06 |       1 |  -178 |
| 2025-07 |       5 |  -824 |
| 2025-08 |       2 |   367 |
| 2025-09 |       4 |  1348 |
| 2025-10 |       2 | -1335 |
| 2025-11 |       3 |    12 |
| 2025-12 |       4 | -2182 |
| 2026-01 |       4 |  1968 |
| 2026-02 |       4 |  -125 |
| 2026-03 |       2 | -1980 |
| 2026-04 |       4 | -3935 |
| 2026-05 |       2 | -1930 |
| 2026-06 |       2 |   191 |
| 2026-07 |       4 |   128 |
| 2026-08 |       3 | -1889 |
| 2026-09 |       1 |  1858 |
| 2026-10 |       1 | -1068 |

## By sector

| sector                         |   count |   sum |
|:-------------------------------|--------:|------:|
| Metals & Mining                |      11 | -4268 |
| Chemicals                      |       4 | -4145 |
| Information Technology         |      12 | -2296 |
| Telecommunication              |       2 | -1961 |
| Consumer Services              |       2 | -1209 |
| Consumer Durables              |       1 | -1136 |
| Automobile and Auto Components |       3 |  -926 |
| Construction Materials         |       1 |  -838 |
| Oil Gas & Consumable Fuels     |       1 |  -178 |
| Financial Services             |       3 |  -133 |
| Fast Moving Consumer Goods     |       3 |  1587 |
| Capital Goods                  |       2 |  1729 |
| Services                       |       1 |  1857 |
| Realty                         |       4 |  1906 |
| Healthcare                     |       2 |  2346 |
| Power                          |       3 |  2359 |
| Construction                   |      11 |  5131 |

## Full grid (chosen by IS_pf)

| decision_time   |   rr | breakeven   |   top_sectors |   IS_trades |   IS_days |   IS_net |   IS_win_rate |   IS_pf |   IS_avg_R |   IS_max_dd |   IS_charges |   OOS_trades |   OOS_days |   OOS_net |   OOS_win_rate |   OOS_pf |   OOS_avg_R |   OOS_max_dd |   OOS_charges |
|:----------------|-----:|:------------|--------------:|------------:|----------:|---------:|--------------:|--------:|-----------:|------------:|-------------:|-------------:|-----------:|----------:|---------------:|---------:|------------:|-------------:|--------------:|
| 09:45           |  1.5 | True        |             1 |          20 |        18 |    -3089 |          45   |    0.73 |     -0.155 |       -4711 |         2089 |           44 |         37 |     -5578 |           31.8 |     0.74 |      -0.127 |        -9015 |          4816 |
| 09:45           |  1.5 | True        |             2 |          44 |        37 |    -5053 |          43.2 |    0.78 |     -0.116 |       -7906 |         4574 |           61 |         47 |    -12334 |           29.5 |     0.62 |      -0.202 |       -13195 |          6681 |
| 09:45           |  1.5 | False       |             1 |          20 |        18 |    -4087 |          45   |    0.67 |     -0.205 |       -5379 |         2089 |           44 |         37 |     -2719 |           40.9 |     0.88 |      -0.062 |        -7278 |          4816 |
| 09:45           |  1.5 | False       |             2 |          44 |        37 |    -6051 |          43.2 |    0.75 |     -0.138 |       -8548 |         4573 |           61 |         47 |     -8603 |           37.7 |     0.74 |      -0.141 |       -10834 |          6681 |
| 09:45           |  2   | True        |             1 |          20 |        18 |    -3793 |          40   |    0.67 |     -0.19  |       -5139 |         2089 |           44 |         37 |     -6203 |           25   |     0.72 |      -0.141 |        -8672 |          4816 |
| 09:45           |  2   | True        |             2 |          44 |        37 |    -8146 |          36.4 |    0.68 |     -0.186 |       -7924 |         4573 |           61 |         47 |    -14984 |           21.3 |     0.55 |      -0.246 |       -15560 |          6681 |
| 09:45           |  2   | False       |             1 |          20 |        18 |    -3476 |          45   |    0.72 |     -0.174 |       -4822 |         2089 |           44 |         37 |     -5183 |           31.8 |     0.8  |      -0.118 |        -6837 |          4816 |
| 09:45           |  2   | False       |             2 |          44 |        37 |    -7830 |          38.6 |    0.71 |     -0.178 |       -7924 |         4573 |           61 |         47 |    -14414 |           27.9 |     0.62 |      -0.236 |       -16068 |          6681 |
| 10:15           |  1.5 | True        |             1 |          30 |        26 |     7729 |          63.3 |    1.72 |      0.258 |       -2186 |         3320 |           36 |         33 |    -10479 |           30.6 |     0.53 |      -0.292 |       -11584 |          3971 |
| 10:15           |  1.5 | True        |             2 |          48 |        37 |     4604 |          56.2 |    1.22 |      0.096 |       -4741 |         5260 |           59 |         49 |     -7973 |           39   |     0.74 |      -0.135 |       -13818 |          6469 |
| 10:15           |  1.5 | False       |             1 |          30 |        26 |     7729 |          63.3 |    1.72 |      0.258 |       -2186 |         3320 |           36 |         33 |     -8561 |           36.1 |     0.62 |      -0.238 |       -10744 |          3970 |
| 10:15           |  1.5 | False       |             2 |          48 |        37 |     4604 |          56.2 |    1.22 |      0.096 |       -4741 |         5260 |           59 |         49 |     -7053 |           42.4 |     0.78 |      -0.12  |       -13319 |          6469 |
| 10:15           |  2   | True        |             1 |          30 |        26 |    10112 |          56.7 |    1.92 |      0.337 |       -2969 |         3320 |           36 |         33 |    -10287 |           25   |     0.54 |      -0.286 |       -11270 |          3971 |
| 10:15           |  2   | True        |             2 |          48 |        37 |     5150 |          47.9 |    1.24 |      0.107 |       -5897 |         5260 |           59 |         49 |     -8784 |           30.5 |     0.73 |      -0.149 |       -13721 |          6468 |
| 10:15           |  2   | False       |             1 |          30 |        26 |     9803 |          60   |    1.84 |      0.327 |       -3665 |         3320 |           36 |         33 |     -9188 |           30.6 |     0.62 |      -0.256 |       -10591 |          3970 |
| 10:15           |  2   | False       |             2 |          48 |        37 |     2845 |          50   |    1.12 |      0.059 |       -7893 |         5260 |           59 |         49 |     -9341 |           33.9 |     0.74 |      -0.158 |       -14040 |          6468 |