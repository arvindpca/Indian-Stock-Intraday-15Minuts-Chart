"""Reliance paper bot ka 'Complete Working Explanation' infographic -> reliance_strategy/RELIANCE_EXPLAINER.png
HTML banata hai, phir Playwright (Chromium) se PNG screenshot leta hai."""
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from final_strategy import prepare  # noqa: E402

DAY = "2026-10-15"
d = prepare(os.path.join(HERE, "data", "RELIANCE_5m.csv"))
x = d[(d.index.normalize() == pd.Timestamp(DAY)) & (d.tod <= 915)].copy()
t = pd.read_csv(os.path.join(HERE, "results", "final_strategy_trades.csv"), parse_dates=["entry_time", "exit_time"])
tr = t[t.entry_time.dt.strftime("%Y-%m-%d") == DAY].iloc[0]
ei = x.index.get_loc(tr.entry_time)
R = float(x.atr.iloc[ei - 1])
SL0 = tr.entry - 2 * R
peak = float(x.high.iloc[ei:x.index.get_loc(tr.exit_time) + 1].max())
gross = (tr.exit - tr.entry) * 100
net = tr.ret_pct / 100 * tr.entry * 100

# trailing SL path (and a real SL-update sample)
sl, stop = [None] * len(x), SL0
for j in range(ei, len(x)):
    sl[j] = stop
    if x.index[j] >= tr.exit_time:
        break
    stop = max(stop, x.close.iloc[j] - 2 * R)
x["sl"] = sl
_k = next(j for j in range(ei, len(x)) if sl[j] is not None and sl[j] - tr.entry >= 15)
UPD_SL, UPD_PX = sl[_k], float(x.close.iloc[_k - 1])


def svg_candles(df, W, H, pad=8, extra=None, ylim=None):
    """df ke candles ka chhota SVG. extra(fx, fy) -> extra svg string."""
    lo, hi = ylim or (df.low.min(), df.high.max())
    rng = hi - lo or 1
    n = len(df)
    cw = (W - 2 * pad) / n
    fx = lambda i: pad + cw * (i + 0.5)
    fy = lambda p: pad + (hi - p) / rng * (H - 2 * pad)
    s = [f'<svg viewBox="0 0 {W} {H}" width="100%" height="{H}">']
    for i, (o, h, l, c) in enumerate(zip(df.open, df.high, df.low, df.close)):
        col = "#16a34a" if c >= o else "#dc2626"
        s.append(f'<line x1="{fx(i):.1f}" x2="{fx(i):.1f}" y1="{fy(h):.1f}" y2="{fy(l):.1f}" stroke="{col}" stroke-width="1"/>')
        y0, y1 = fy(max(o, c)), fy(min(o, c))
        s.append(f'<rect x="{fx(i) - cw * 0.32:.1f}" y="{y0:.1f}" width="{cw * 0.64:.1f}" height="{max(y1 - y0, 1):.1f}" fill="{col}"/>')
    if extra:
        s.append(extra(fx, fy, cw))
    s.append("</svg>")
    return "".join(s)


early = x.iloc[:22]                         # 9:15 - 11:00
sig_i = ei - 1


def lbl(lines):
    return "".join(f'<text x="10" y="{18 + k * 15}" font-size="{fs}" fill="{c}" font-weight="{w}">{t}</text>'
                   for k, (t, c, fs, w) in enumerate(lines))


def ex_atr(fx, fy, cw):
    i = 9
    h, l = early.high.iloc[i], early.low.iloc[i]
    return (f'<line x1="{fx(i) + cw:.1f}" x2="{fx(i) + cw:.1f}" y1="{fy(h):.1f}" y2="{fy(l):.1f}" stroke="#2563eb" stroke-width="2.5" marker-start="url(#a)" marker-end="url(#a)"/>'
            + lbl([("↕ 1 candle ka range", "#1d4ed8", 13, 800), ("14 candle ka average = ATR", "#1d4ed8", 12, 600),
                   (f"Yahan ATR ≈ ₹{R:.2f}", "#1d4ed8", 12, 600)]))


def ex_move(fx, fy, cw):
    a, b = sig_i - 4, sig_i
    return (f'<rect x="{fx(max(a, 0)) - cw / 2:.1f}" y="4" width="{cw * (b - max(a, 0) + 1):.1f}" height="96%" fill="#7c3aed" opacity="0.12"/>'
            f'<text x="{fx(b) + cw:.1f}" y="22" font-size="13" fill="#6d28d9" font-weight="800">Move = +{early.move.iloc[b]:.1f} × ATR</text>'
            f'<text x="{fx(b) + cw:.1f}" y="38" font-size="11.5" fill="#6d28d9">(4 candle = 20 min)</text>')


def ex_signal(fx, fy, cw):
    return (f'<circle cx="{fx(sig_i):.1f}" cy="{fy(early.close.iloc[sig_i]):.1f}" r="8" fill="none" stroke="#ea580c" stroke-width="3"/>'
            + lbl([("◯ BUY signal (9:30)", "#ea580c", 13, 800), (f"Move +{early.move.iloc[sig_i]:.1f} > +1.5 ✔", "#ea580c", 12, 600),
                   ("Time 9:30–14:30 ✔", "#ea580c", 12, 600)]))


def ex_entry(fx, fy, cw):
    return (f'<line x1="{fx(ei):.1f}" x2="{fx(len(early) - 1):.1f}" y1="{fy(SL0):.1f}" y2="{fy(SL0):.1f}" stroke="#dc2626" stroke-dasharray="5,3" stroke-width="2"/>'
            f'<polygon points="{fx(ei):.1f},{fy(tr.entry) + 4:.1f} {fx(ei) - 7:.1f},{fy(tr.entry) + 16:.1f} {fx(ei) + 7:.1f},{fy(tr.entry) + 16:.1f}" fill="#16a34a"/>'
            + lbl([(f"▲ Entry ₹{tr.entry:.1f}", "#15803d", 13, 800), ("(agli candle ka open)", "#15803d", 11.5, 600),
                   (f"- - SL ₹{SL0:.1f} (2×ATR)", "#dc2626", 12.5, 800)]))


rows = "".join(
    f"<tr style='{'background:#fef3c7;font-weight:700' if i == sig_i else ''}'><td>{ts:%H:%M}</td><td>{r.open:.1f}</td><td>{r.high:.1f}</td><td>{r.low:.1f}</td><td>{r.close:.1f}</td><td>{r.atr:.2f}</td><td>{r.move:+.2f}</td><td>{'BUY signal' if i == sig_i else ('Entry @ open' if i == ei else '')}</td></tr>"
    for i, (ts, r) in enumerate(x.iloc[:ei + 2].iterrows()) if i >= 1)
sample_tbl = ("<table style='margin-top:4px'><tr><th>Time</th><th>Open</th><th>High</th><th>Low</th><th>Close</th><th>ATR</th><th>Move</th><th></th></tr>"
              + rows + "</table>")
move_big = svg_candles(early, 420, 235, extra=ex_move)
lo_e = min(early.low.min(), SL0) - 1
mini = {k: svg_candles(early, 210, 320, extra=f, ylim=(lo_e, early.high.max() + 9))
        for k, f in [("atr", ex_atr), ("move", ex_move), ("signal", ex_signal), ("entry", ex_entry)]}


def ex_full(fx, fy, cw):
    pts = " ".join(f"{fx(i):.1f},{fy(v):.1f}" for i, v in enumerate(x.sl) if v is not None)
    xe = x.index.get_loc(tr.exit_time)
    return (f'<polyline fill="none" stroke="#f59e0b" stroke-width="2.4" points="{pts}"/>'
            f'<text x="{fx(ei + 14):.1f}" y="{fy(1195):.1f}" font-size="12" fill="#b45309" font-weight="700">Trailing SL (Close − 2×ATR)</text>'
            f'<polygon points="{fx(ei):.1f},{fy(tr.entry) + 4:.1f} {fx(ei) - 6:.1f},{fy(tr.entry) + 15:.1f} {fx(ei) + 6:.1f},{fy(tr.entry) + 15:.1f}" fill="#16a34a"/>'
            f'<text x="{fx(ei) + 8:.1f}" y="{fy(tr.entry) + 6:.1f}" font-size="12" fill="#15803d" font-weight="800">BUY</text>'
            f'<circle cx="{fx(xe):.1f}" cy="{fy(tr.exit):.1f}" r="6" fill="#f59e0b" stroke="#fff" stroke-width="1.5"/>'
            f'<text x="{fx(xe) - 70:.1f}" y="{fy(tr.exit) + 22:.1f}" font-size="12" fill="#b45309" font-weight="800">EXIT (SL hit)</text>')


full = svg_candles(x, 420, 190, extra=ex_full, ylim=(SL0 - 2, x.high.max() + 2))

HTML = f"""<!doctype html><html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{width:1600px;background:#fff;font-family:'DejaVu Sans',Arial,sans-serif;color:#111827;padding:10px}}
.title{{background:linear-gradient(90deg,#0b2a6b,#1e3a8a);color:#fff;text-align:center;border-radius:10px;padding:8px;font-size:30px;font-weight:800}}
.title span{{color:#fbbf24}} .title small{{font-size:24px;font-weight:600}}
.sub{{background:#fef9c3;border:2px solid #facc15;border-radius:8px;margin:6px 0;padding:6px;text-align:center;font-size:15.5px;font-weight:700;line-height:1.45}}
.grid{{display:grid;gap:8px}}
.p{{border:2px solid var(--c);border-radius:10px;overflow:hidden;background:#fff}}
.h{{background:var(--c);color:#fff;font-size:20px;font-weight:800;padding:5px 10px;display:flex;align-items:center;gap:10px}}
.n{{background:#fff;color:var(--c);border-radius:50%;width:32px;height:32px;display:flex;align-items:center;justify-content:center;font-size:20px;flex:none}}
.b{{padding:8px 10px;font-size:13.2px;line-height:1.45}}
.blue{{--c:#1d4ed8}} .green{{--c:#15803d}} .purple{{--c:#6d28d9}} .red{{--c:#dc2626}} .teal{{--c:#0e7490}} .dgreen{{--c:#166534}} .orange{{--c:#ea580c}} .violet{{--c:#7e22ce}} .crimson{{--c:#be123c}}
.flow{{display:flex;gap:8px}} .rep{{align-self:center;background:#fef3c7;border:2px solid #f59e0b;border-radius:8px;padding:8px 6px;text-align:center;font-weight:800;font-size:15px;width:96px}}
.rep small{{display:block;font-weight:600;font-size:11.5px;margin-top:4px}}
.steps{{flex:1;display:flex;flex-direction:column;align-items:center}}
.st{{width:100%;border:2px solid;border-radius:7px;text-align:center;padding:3px 4px;font-weight:700;font-size:13px}}
.st small{{display:block;font-weight:400;font-size:11.2px}}
.ar{{color:#334155;font-size:13px;line-height:12px}}
table{{border-collapse:collapse;width:100%;font-size:12.6px}}
th{{background:#dbeafe;text-align:left;padding:4px 6px;border:1px solid #94a3b8}} td{{padding:3.4px 6px;border:1px solid #cbd5e1}}
td:first-child{{font-weight:700;font-family:'DejaVu Sans Mono',monospace;font-size:11.8px}}
code,pre{{font-family:'DejaVu Sans Mono',monospace}}
pre{{background:#1e293b;color:#e2e8f0;border-radius:8px;padding:8px;font-size:11.3px;line-height:1.5;white-space:pre}}
.k{{color:#c084fc}} .s{{color:#fcd34d}} .f{{color:#7dd3fc}}
.phone{{width:230px;background:#0f172a;border-radius:26px;padding:9px;flex:none}}
.scr{{background:#e2e8f0;border-radius:18px;overflow:hidden;height:100%}}
.tgh{{background:#229ED9;color:#fff;font-weight:700;padding:7px 10px;font-size:14px}}
.msg{{background:#fff;margin:6px;border-radius:9px;padding:6px 8px;font-size:11.2px;line-height:1.4}}
.msg b{{font-size:11.8px}}
.box{{border:1.5px solid;border-radius:8px;padding:6px 9px}}
.url{{background:#fef3c7;border:1.5px solid #f59e0b;border-radius:6px;padding:4px 8px;font-family:'DejaVu Sans Mono',monospace;font-weight:700;font-size:12.5px;margin:4px 0 6px}}
ul{{padding-left:16px}} ol{{padding-left:18px}}
.logic{{display:grid;grid-template-columns:repeat(4,1fr);gap:6px}}
.lg{{border-radius:8px;padding:6px 8px;font-size:12.6px;line-height:1.4}}
.lg h4{{font-size:15px;margin-bottom:2px}}
.chev{{display:flex;align-items:center;justify-content:center;color:#16a34a;font-size:30px;font-weight:900}}
.bar{{background:#dbeafe;border:1.5px solid #3b82f6;border-radius:8px;text-align:center;font-weight:800;font-size:15px;padding:6px;margin-top:6px;color:#1e3a8a}}
.ok li{{list-style:none;margin-left:-14px}} .ok li:before{{content:'✅ ';}}
.file{{display:flex;gap:8px;align-items:flex-start;margin-bottom:6px}}
.ic{{width:44px;height:50px;border-radius:6px;color:#fff;font-weight:800;font-size:11px;display:flex;align-items:center;justify-content:center;flex:none}}
</style></head><body>
<div class="title">Reliance_Paper_Live.py <span>(V1)</span> <small>– Complete Working Explanation</small></div>
<div class="sub">Ye ek PAPER TRADING bot hai — NSE: RELIANCE (Cash), 5-minute chart. Koi asli order nahi lagta.<br>
20-Minute Momentum (Move &gt; 1.5×ATR) se entry + 2×ATR Trailing Stop se exit, aur Angel One data + GitHub Actions se har 5 min Telegram alert (Entry / SL / Exit / PnL).</div>

<div class="grid" style="grid-template-columns:395px 1fr 535px">
 <div class="p blue"><div class="h"><div class="n">1</div>Overall Flow / Kaise Chale</div><div class="b">
  <div class="flow"><div class="rep">Repeat<br>Every<br>5 Minutes<small>9:30 – 15:45<br>Mon – Fri<br>(GitHub Actions)</small></div>
  <div class="steps">
   <div class="st" style="background:#bbf7d0;border-color:#16a34a">Start Script <small>(GitHub cron trigger)</small></div><div class="ar">▼</div>
   <div class="st" style="background:#dbeafe;border-color:#3b82f6">Angel One Login <small>(API key + TOTP)</small></div><div class="ar">▼</div>
   <div class="st" style="background:#fee2e2;border-color:#ef4444">Download 5-min Candles <small>(last 7 days, sirf poori candles)</small></div><div class="ar">▼</div>
   <div class="st" style="background:#ede9fe;border-color:#8b5cf6">Calculate ATR(14) + 20-min Move <small>Move = (Close − Close 4 candle pehle) ÷ ATR</small></div><div class="ar">▼</div>
   <div class="st" style="background:#fef3c7;border-color:#f59e0b">Generate Signal <small>Move &gt; +1.5 BUY • &lt; −1.5 SELL</small></div><div class="ar">▼</div>
   <div class="st" style="background:#dcfce7;border-color:#22c55e">Manage Open Trade <small>(Trailing SL / 15:15 exit)</small></div><div class="ar">▼</div>
   <div class="st" style="background:#dbeafe;border-color:#3b82f6">Send Telegram Alert <small>(Entry / SL update / Exit / PnL)</small></div><div class="ar">▼</div>
   <div class="st" style="background:#fee2e2;border-color:#ef4444">Save Trade to CSV <small>(paper_trades.csv + state.json)</small></div>
  </div></div></div></div>

 <div class="p green"><div class="h"><div class="n">2</div>Key Settings (Configuration)</div><div class="b">
  <table><tr><th>Parameter</th><th>Value</th><th>Meaning</th></tr>
  <tr><td>SYMBOL</td><td>RELIANCE-EQ (2885)</td><td>NSE cash, Angel One token</td></tr>
  <tr><td>TIMEFRAME</td><td>5 minute</td><td>Har candle 5 min ki</td></tr>
  <tr><td>ATR PERIOD</td><td>14</td><td>Normal hilna (volatility)</td></tr>
  <tr><td>LOOKBACK</td><td>4 candles</td><td>20 min ka move dekhna</td></tr>
  <tr><td>THRESH</td><td>1.5</td><td>Move &gt; 1.5×ATR = signal</td></tr>
  <tr><td>TRAIL_ATR</td><td>2.0</td><td>SL = 2×ATR door, trailing</td></tr>
  <tr><td>START</td><td>09:30</td><td>Isse pehle entry nahi</td></tr>
  <tr><td>LAST_ENTRY</td><td>14:30</td><td>Iske baad nayi entry nahi</td></tr>
  <tr><td>EXIT</td><td>15:15</td><td>Har position band</td></tr>
  <tr><td>MAX TRADES</td><td>1 / din</td><td>Din ka pehla signal hi</td></tr>
  <tr><td>COST</td><td>0.0006 (0.06%)</td><td>Charges + slippage (round trip)</td></tr>
  <tr><td>PAPER_QTY</td><td>100 shares</td><td>₹ P&amp;L dikhane ke liye</td></tr>
  </table></div></div>

 <div class="p purple"><div class="h"><div class="n">3</div>Telegram Alerts</div><div class="b" style="display:flex;gap:8px">
  <div style="flex:1">Environment se <b>TELEGRAM_BOT_TOKEN</b> aur <b>TELEGRAM_CHAT_ID</b> leta hai (GitHub secrets).
  <pre style="margin:6px 0"><span class="k">def</span> <span class="f">tg</span>(text):
    tok = _env(<span class="s">"TELEGRAM_BOT_TOKEN"</span>)
    chat = _env(<span class="s">"TELEGRAM_CHAT_ID"</span>)
    <span class="k">if not</span> (tok <span class="k">and</span> chat): <span class="k">return</span>
    requests.post(
      <span class="s">f"https://api.telegram.org/</span>
<span class="s">        bot{{tok}}/sendMessage"</span>,
      data={{<span class="s">"chat_id"</span>: chat,
            <span class="s">"text"</span>: text,
            <span class="s">"parse_mode"</span>: <span class="s">"HTML"</span>}})</pre>
  <b>Alerts kab aate hain:</b><ul><li>Nayi Entry (BUY / SELL + SL)</li><li>SL Update (har 1×ATR khiskne pe)</li><li>Exit + PnL + ab tak ka total</li><li>"Aaj koi signal nahi" (3:15 ke baad)</li></ul></div>
  <div class="phone"><div class="scr"><div class="tgh">✈ Telegram</div>
   <div class="msg"><b>🟢 RELIANCE PAPER BUY</b><br>Entry: ₹{tr.entry:.1f} (09:35)<br>Stop loss: ₹{SL0:.1f}<br>Target: nahi — trailing SL</div>
   <div class="msg"><b>🔁 SL update</b> 🟢 BUY<br>Naya SL: ₹{UPD_SL:.1f}<br>Price: ₹{UPD_PX:.1f}<br>Profit lock: ₹{UPD_SL - tr.entry:+.1f}/share</div>
   <div class="msg"><b>🏁 RELIANCE PAPER EXIT</b><br>₹{tr.entry:.1f} → ₹{tr.exit:.1f} (14:20)<br>Wajah: Trailing SL hit<br>Result: <b style="color:#16a34a">{tr.ret_pct:+.2f}% | ₹{net:+,.0f}</b></div>
  </div></div></div></div>
</div>

<div class="grid" style="grid-template-columns:1fr 1fr;margin-top:8px">
 <div class="p red"><div class="h"><div class="n">4</div>Data Download (Angel One SmartAPI)</div><div class="b">
  Angel One SmartAPI se login karke 5-min candles laata hai (wahi secrets jo aapke baaki scanners use karte hain).
  <div class="url">SmartConnect.getCandleData(...)</div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
   <div class="box" style="border-color:#3b82f6;background:#eff6ff"><b>Parameters:</b><ul>
    <li>exchange = NSE</li><li>symboltoken = 2885 (RELIANCE-EQ)</li><li>interval = FIVE_MINUTE</li><li>fromdate = aaj − 7 din, 09:15</li><li>todate = abhi ka time</li></ul></div>
   <div class="box" style="border-color:#f59e0b;background:#fffbeb"><b>Process:</b><ol>
    <li>Login (Client ID + MPIN + TOTP)</li><li>Candles download (3 retry)</li><li>DataFrame me convert</li><li>Adhuri (chalu) candle hatao</li><li>Aaj ka din shuru se dobara simulate</li></ol></div>
  </div>
  <div style="margin-top:6px"><b>Asli data ka sample (Reliance {pd.Timestamp(DAY):%d %b}, 5-min) — Move column bot khud nikaalta hai:</b>
  {sample_tbl}</div></div></div>

 <div class="p teal"><div class="h"><div class="n">5</div>ATR &amp; 20-Min Move Detection</div><div class="b" style="display:flex;gap:10px">
  <div style="flex:1.2">Reliance {pd.Timestamp(DAY):%d %b}, 9:15–11:00 (asli candles)
   {move_big}</div>
  <div class="box" style="flex:1;border-color:#0e7490;background:#ecfeff"><b style="color:#0e7490;font-size:15px">Logic:</b><ul>
   <li><b>ATR(14)</b> = pichli 14 candle ka average range. Reliance ke liye aksar ₹2–4.</li>
   <li><b>Move</b> = 4 candle (20 min) me kitna chala ÷ ATR</li>
   <li>Move +1.5 se upar = tez upar bhaaga → BUY</li>
   <li>Move −1.5 se neeche = tez gira → SELL</li></ul>
   ATR se divide isliye, taaki shaant aur tez dono din pe same rule chale.</div>
 </div></div>
</div>

<div class="grid" style="grid-template-columns:1fr 430px 380px;margin-top:8px">
 <div class="p dgreen"><div class="h"><div class="n">6</div>Strategy Logic (Momentum + Trailing Stop)</div><div class="b">
  <div class="logic">
   <div class="lg" style="background:#eff6ff"><h4 style="color:#1d4ed8">1. ATR</h4>Reliance normally ek candle me kitna hilta hai — ye "ruler" hai.{mini['atr']}</div>
   <div class="lg" style="background:#f5f3ff"><h4 style="color:#6d28d9">2. 20-Min Move</h4>Pichle 4 candle ka move, ATR ke hisaab se naapo.{mini['move']}</div>
   <div class="lg" style="background:#fff7ed"><h4 style="color:#ea580c">3. Signal</h4>Move &gt; +1.5 → BUY, Move &lt; −1.5 → SELL. 9:30–14:30, din me 1.{mini['signal']}</div>
   <div class="lg" style="background:#f0fdf4"><h4 style="color:#15803d">4. Entry (5-Min)</h4>Signal candle band → agli candle ke OPEN pe entry, SL 2×ATR.{mini['entry']}</div>
  </div>
  <div class="bar">Complete Setup: ATR → 20-Min Move → Signal (±1.5) → Next Candle Entry → 2×ATR Trailing SL → Exit (SL / 15:15)</div>
 </div></div>

 <div class="p orange"><div class="h"><div class="n">7</div>Trade Management</div><div class="b">
  Entry ke baad bot khud trade sambhalta hai:<ul>
   <li>Stop loss: entry se <b>2×ATR</b> door</li>
   <li>Har candle close pe: SL = Close − 2×ATR (BUY) / Close + 2×ATR (SELL)</li>
   <li>SL sirf favour me khiskta hai, kabhi peeche nahi</li>
   <li><b>Koi fixed target nahi</b> — bada move chalne do</li>
   <li>15:15 pe jo bacha, band • Din me sirf 1 trade</li></ul>
  {full}
  <div class="box" style="border-color:#f59e0b;background:#fffbeb;font-size:12.6px"><b>Example ({pd.Timestamp(DAY):%d %b}):</b>
   Entry ₹{tr.entry:.1f} • ATR ₹{R:.2f} • SL ₹{SL0:.1f} (risk ₹{2 * R:.1f})<br>
   Price upar ₹{peak:.1f} tak gaya, SL khiskte-khiskte ₹{tr.exit:.1f} pe hit<br>
   Profit ₹{tr.exit - tr.entry:.1f}/share × 100 = ₹{gross:,.0f} → charges ke baad <b>₹{net:,.0f} ({tr.ret_pct:+.2f}%)</b></div>
 </div></div>

 <div style="display:flex;flex-direction:column;gap:8px">
  <div class="p violet"><div class="h"><div class="n">8</div>Files Used</div><div class="b">
   <div class="file"><div class="ic" style="background:#2563eb">JSON</div><div><b>paper/state.json</b><br>Aaj ka trade, kaun se alert ja chuke (duplicate alert nahi)</div></div>
   <div class="file"><div class="ic" style="background:#16a34a">CSV</div><div><b>paper/paper_trades.csv</b><br>Har trade: date, side, entry, exit, wajah, %, ₹</div></div>
   <div class="file"><div class="ic" style="background:#475569">MD</div><div><b>paper/STATUS.md</b> — win %, total, Profit Factor<br><b>final_strategy.py</b> — rules + backtest</div></div>
   <div class="file"><div class="ic" style="background:#111827">YML</div><div><b>.github/workflows/reliance-paper.yml</b><br>Har 5 min chalane wala schedule</div></div>
  </div></div>
  <div class="p crimson"><div class="h"><div class="n">9</div>Key Points to Remember</div><div class="b"><ul class="ok">
   <li>Sirf PAPER TRADING — koi asli order nahi</li>
   <li>Angel One ka LIVE 5-min data</li>
   <li>Backtest 1 saal: 254 trades, 39% win, PF 1.28, +16.8%</li>
   <li>Unseen aakhri 4 mahine: PF 1.27, +5.5%</li>
   <li>10 me ~6 trade chhote loss; 9 loss lagatar tak aaye</li>
   <li>Sirf Reliance pe test — dusre stock pe nahi</li>
   <li>1–2 mahine paper ke baad hi live socho</li></ul></div></div>
 </div>
</div>
</body></html>"""

html_path = os.path.join(HERE, "RELIANCE_EXPLAINER.html")
with open(html_path, "w") as f:
    f.write(HTML.replace('<svg viewBox', '<svg><defs><marker id="a" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0,0L10,5L0,10z" fill="#2563eb"/></marker></defs></svg><svg viewBox', 1))

from playwright.sync_api import sync_playwright  # noqa: E402
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1620, "height": 1000}, device_scale_factor=2)
    pg.goto("file://" + html_path)
    pg.wait_for_timeout(300)
    out = os.path.join(HERE, "RELIANCE_EXPLAINER.png")
    pg.locator("body").screenshot(path=out)
    b.close()
print("saved", out)
