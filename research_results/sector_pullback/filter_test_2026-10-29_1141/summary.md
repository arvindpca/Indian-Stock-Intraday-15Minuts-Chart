# Sector Pullback - Nifty / Sector filter test

IS 2023-10-12 -> 2025-10-17 | OOS 2025-10-20 -> 2026-10-26 | Rs 1,000 risk sizing, after charges

| exit                      | filters                |   IS_trades |   IS_net |   IS_win |   OOS_trades |   OOS_net |   OOS_win |   OOS_pf |   OOS_dd |
|:--------------------------|:-----------------------|------------:|---------:|---------:|-------------:|----------:|----------:|---------:|---------:|
| SL + 1:2 target           | Nifty ON + Sector ON   |          66 |    -5812 |     39.4 |           80 |     -7355 |      37.5 |     0.84 |   -12835 |
| SL + 1:2 target           | Nifty ON, Sector OFF   |         246 |   -49967 |     35.4 |          315 |    -46525 |      35.2 |     0.75 |   -52401 |
| SL + 1:2 target           | Nifty OFF, Sector ON   |         126 |   -30406 |     31   |          150 |    -25696 |      35.3 |     0.72 |   -28765 |
| SL + 1:2 target           | Nifty OFF + Sector OFF |         452 |   -86103 |     35   |          531 |    -91895 |      34.7 |     0.72 |   -97320 |
| No SL/TP, hold till 15:00 | Nifty ON + Sector ON   |          66 |    16447 |     54.5 |           80 |      -484 |      45   |     0.99 |   -22776 |
| No SL/TP, hold till 15:00 | Nifty ON, Sector OFF   |         251 |     7132 |     49   |          330 |    -35208 |      47.6 |     0.88 |   -58425 |
| No SL/TP, hold till 15:00 | Nifty OFF, Sector ON   |         126 |     2607 |     47.6 |          152 |    -31534 |      42.1 |     0.8  |   -48172 |
| No SL/TP, hold till 15:00 | Nifty OFF + Sector OFF |         464 |    30636 |     47.4 |          550 |    -81449 |      46.9 |     0.84 |  -105791 |

## Worst single trades (no SL/TP)

| filters                |   worst trade |   5% worst |
|:-----------------------|--------------:|-----------:|
| Nifty OFF + Sector OFF |        -14779 |      -3986 |
| Nifty OFF, Sector ON   |        -13806 |      -3865 |
| Nifty ON + Sector ON   |         -5138 |      -3474 |
| Nifty ON, Sector OFF   |         -8581 |      -3963 |

## By side

|                                                    |   count |    sum |
|:---------------------------------------------------|--------:|-------:|
| ('eod', 'Nifty OFF + Sector OFF', 'IS', 'LONG')    |     262 |  43087 |
| ('eod', 'Nifty OFF + Sector OFF', 'IS', 'SHORT')   |     202 | -12451 |
| ('eod', 'Nifty OFF + Sector OFF', 'OOS', 'LONG')   |     302 | -15351 |
| ('eod', 'Nifty OFF + Sector OFF', 'OOS', 'SHORT')  |     248 | -66098 |
| ('eod', 'Nifty OFF, Sector ON', 'IS', 'LONG')      |      82 |  -5232 |
| ('eod', 'Nifty OFF, Sector ON', 'IS', 'SHORT')     |      44 |   7839 |
| ('eod', 'Nifty OFF, Sector ON', 'OOS', 'LONG')     |     100 | -42964 |
| ('eod', 'Nifty OFF, Sector ON', 'OOS', 'SHORT')    |      52 |  11429 |
| ('eod', 'Nifty ON + Sector ON', 'IS', 'LONG')      |      39 |  10136 |
| ('eod', 'Nifty ON + Sector ON', 'IS', 'SHORT')     |      27 |   6311 |
| ('eod', 'Nifty ON + Sector ON', 'OOS', 'LONG')     |      50 | -10626 |
| ('eod', 'Nifty ON + Sector ON', 'OOS', 'SHORT')    |      30 |  10141 |
| ('eod', 'Nifty ON, Sector OFF', 'IS', 'LONG')      |     142 |  19804 |
| ('eod', 'Nifty ON, Sector OFF', 'IS', 'SHORT')     |     109 | -12671 |
| ('eod', 'Nifty ON, Sector OFF', 'OOS', 'LONG')     |     174 |   7653 |
| ('eod', 'Nifty ON, Sector OFF', 'OOS', 'SHORT')    |     156 | -42861 |
| ('sltp', 'Nifty OFF + Sector OFF', 'IS', 'LONG')   |     254 | -73942 |
| ('sltp', 'Nifty OFF + Sector OFF', 'IS', 'SHORT')  |     198 | -12161 |
| ('sltp', 'Nifty OFF + Sector OFF', 'OOS', 'LONG')  |     289 | -61452 |
| ('sltp', 'Nifty OFF + Sector OFF', 'OOS', 'SHORT') |     242 | -30443 |
| ('sltp', 'Nifty OFF, Sector ON', 'IS', 'LONG')     |      82 | -29214 |
| ('sltp', 'Nifty OFF, Sector ON', 'IS', 'SHORT')    |      44 |  -1192 |
| ('sltp', 'Nifty OFF, Sector ON', 'OOS', 'LONG')    |      99 | -26906 |
| ('sltp', 'Nifty OFF, Sector ON', 'OOS', 'SHORT')   |      51 |   1210 |
| ('sltp', 'Nifty ON + Sector ON', 'IS', 'LONG')     |      39 | -11032 |
| ('sltp', 'Nifty ON + Sector ON', 'IS', 'SHORT')    |      27 |   5219 |
| ('sltp', 'Nifty ON + Sector ON', 'OOS', 'LONG')    |      50 | -14680 |
| ('sltp', 'Nifty ON + Sector ON', 'OOS', 'SHORT')   |      30 |   7325 |
| ('sltp', 'Nifty ON, Sector OFF', 'IS', 'LONG')     |     140 | -42609 |
| ('sltp', 'Nifty ON, Sector OFF', 'IS', 'SHORT')    |     106 |  -7357 |
| ('sltp', 'Nifty ON, Sector OFF', 'OOS', 'LONG')    |     164 | -38906 |
| ('sltp', 'Nifty ON, Sector OFF', 'OOS', 'SHORT')   |     151 |  -7619 |