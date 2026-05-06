"""
Generate HTML report of all Kronos decisions with TradingView links.
Run: python scripts/kronos_report.py [output.html]
"""
import os
import sys
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import psycopg2
import psycopg2.extras

DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://fvg:f18bbdd5a18785da8d18d8d92965defc@localhost:5432/fvg",
)

WIB = timezone(timedelta(hours=7))

INTERVAL_MAP = {"15m": "15", "30m": "30", "1h": "60", "2h": "120", "4h": "240"}


def tv_link(symbol: str, tf: str, ts_ms: int = None) -> str:
    """TradingView chart URL with optional time anchor."""
    iv = INTERVAL_MAP.get(tf, "60")
    base = f"https://www.tradingview.com/chart/?symbol=BINANCE:{symbol}.P&interval={iv}"
    return base


def tv_embed(symbol: str, tf: str, container_id: str) -> str:
    """TradingView Advanced Chart widget HTML."""
    iv = INTERVAL_MAP.get(tf, "60")
    return f"""
<div class="tv-wrap" id="{container_id}">
  <div id="tv-{container_id}" style="height:600px;"></div>
  <script type="text/javascript">
    new TradingView.widget({{
      "container_id": "tv-{container_id}",
      "symbol": "BINANCE:{symbol}.P",
      "interval": "{iv}",
      "timezone": "Asia/Jakarta",
      "theme": "dark",
      "style": "1",
      "locale": "en",
      "toolbar_bg": "#1e1e1e",
      "enable_publishing": false,
      "withdateranges": true,
      "hide_side_toolbar": false,
      "allow_symbol_change": true,
      "studies": [
        "STD;RSI",
        "STD;Stochastic_RSI",
        "MAExp@tv-basicstudies",
        "MASimple@tv-basicstudies",
        "BB@tv-basicstudies",
        "Volume@tv-basicstudies"
      ],
      "autosize": true
    }});
  </script>
</div>
"""


def fmt_price(v) -> str:
    if v is None:
        return "—"
    f = float(v)
    if f >= 1000:
        return f"{f:,.2f}"
    if f >= 1:
        return f"{f:.4f}"
    return f"{f:.6f}"


def fmt_ts(ts_ms) -> str:
    if ts_ms is None:
        return "—"
    dt = datetime.fromtimestamp(int(ts_ms) / 1000, tz=WIB)
    return dt.strftime("%Y-%m-%d %H:%M WIB")


def status_chip(status: str) -> str:
    color = {
        "win": "#22c55e",
        "loss": "#ef4444",
        "tp1_hit": "#eab308",
        "open": "#3b82f6",
        "SKIP: RANGING": "#6b7280",
        "SKIP: KRONOS CONFLICT": "#a855f7",
    }.get(status, "#6b7280")
    return f'<span class="chip" style="background:{color}">{status}</span>'


def pnl_pct(row) -> str:
    if row["trade_status"] not in ("win", "loss"):
        return "—"
    try:
        entry = float(row["entry"])
        if row["trade_status"] == "win":
            exit_p = float(row["tp2"])
        else:
            exit_p = float(row["sl"])
        if row["direction"] == "long":
            pct = (exit_p - entry) / entry * 100
        else:
            pct = (entry - exit_p) / entry * 100
        sign = "+" if pct >= 0 else ""
        color = "#22c55e" if pct >= 0 else "#ef4444"
        return f'<span style="color:{color}">{sign}{pct:.2f}%</span>'
    except Exception:
        return "—"


def fetch():
    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute("""
              SELECT
                k.id, k.created_at, k.symbol, k.tf, k.event_type, k.zone_dir,
                k.current_price, k.status, k.valid, k.mode, k.direction,
                k.entry, k.sl, k.tp1, k.tp2, k.kronos_raw, k.trade_id,
                s.status AS trade_status, s.closed_at,
                f.strength AS fvg_strength, f.rsi AS fvg_rsi, f.atr AS fvg_atr,
                f.zone_top, f.zone_bottom
              FROM kronos_decisions k
              LEFT JOIN sim_trades s ON s.id = k.trade_id
              LEFT JOIN fvg_zones f ON f.id = k.fvg_id
              WHERE k.source = 'kronos'
              ORDER BY k.created_at DESC
            """)
            return cur.fetchall()
    finally:
        conn.close()


def build_html(rows):
    total = len(rows)
    valid = sum(1 for r in rows if r["valid"])
    skipped = total - valid
    closed = [r for r in rows if r["trade_status"] in ("win", "loss")]
    wins = sum(1 for r in closed if r["trade_status"] == "win")
    losses = sum(1 for r in closed if r["trade_status"] == "loss")
    wr = (wins / len(closed) * 100) if closed else 0.0

    table_rows_fixed = []
    for r in rows:
        kr = r["kronos_raw"] or {}
        if isinstance(kr, str):
            try:
                kr = json.loads(kr)
            except Exception:
                kr = {}
        conf = kr.get("confidence")
        k_dir = kr.get("direction", "")
        k_tf = kr.get("timeframe", "")
        zone_dir_text = "🟢 BULL" if r["zone_dir"] == 1 else "🔴 BEAR"
        trade_status = r["trade_status"] or ("VALID" if r["valid"] else "SKIP")
        chart_id = r["id"].replace("-", "_").replace(":", "_")[:80]
        decision_color = "#22c55e" if r["valid"] else "#6b7280"
        rsi_str = f"{r['fvg_rsi']:.1f}" if r['fvg_rsi'] is not None else "—"
        conf_str = str(conf) if conf is not None else "—"

        # Fallback to kronos_raw when decision wasn't executed (SKIP rows)
        entry_v = r['entry'] if r['entry'] is not None else kr.get('entry')
        sl_v    = r['sl']    if r['sl']    is not None else kr.get('sl')
        tp1_v   = r['tp1']   if r['tp1']   is not None else kr.get('tp1')
        tp2_v   = r['tp2']   if r['tp2']   is not None else kr.get('tp2')
        dir_v   = r['direction'] or (k_dir.lower() if k_dir else None)
        dir_disp = dir_v if dir_v else "—"
        if not r['direction'] and dir_v:
            dir_disp = f'<span style="color:#94a3b8">{dir_v}*</span>'

        row_html = f"""
        <tr class="row" data-status="{trade_status}" data-symbol="{r['symbol']}" data-tf="{r['tf']}" data-mode="{r['mode'] or ''}" data-dir="{r['direction'] or ''}">
          <td class="time">{fmt_ts(r['created_at'])}</td>
          <td class="symbol"><strong>{r['symbol']}</strong></td>
          <td>{r['tf']}</td>
          <td>{r['event_type']}</td>
          <td>{zone_dir_text}</td>
          <td><span style="color:{decision_color}">{r['status']}</span></td>
          <td>{conf_str}</td>
          <td>{k_dir} / {k_tf}</td>
          <td>{r['mode'] or (k_tf.lower() if k_tf else '—')}</td>
          <td>{dir_disp}</td>
          <td>{fmt_price(entry_v)}</td>
          <td>{fmt_price(sl_v)}</td>
          <td>{fmt_price(tp1_v)}</td>
          <td>{fmt_price(tp2_v)}</td>
          <td>{r['fvg_strength'] or '—'}</td>
          <td>{rsi_str}</td>
          <td>{status_chip(trade_status)}</td>
          <td>{pnl_pct(r)}</td>
          <td>{fmt_ts(r['closed_at']) if r['closed_at'] else '—'}</td>
          <td>
            <a href="{tv_link(r['symbol'], r['tf'])}" target="_blank">TV</a>
            <button onclick="toggleChart('{chart_id}', '{r['symbol']}', '{INTERVAL_MAP.get(r['tf'], '60')}')">Chart</button>
          </td>
        </tr>
        <tr class="chart-row" id="chart-{chart_id}" style="display:none">
          <td colspan="20"><div id="tv-host-{chart_id}" style="height:600px;background:#1e1e1e"></div></td>
        </tr>
        """
        table_rows_fixed.append(row_html)

    table_body = "\n".join(table_rows_fixed)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Kronos Decision Report — fvg-alpha-caller</title>
<script src="https://s3.tradingview.com/tv.js"></script>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
    background: #0f172a; color: #e2e8f0;
    margin: 0; padding: 24px;
  }}
  h1 {{ margin: 0 0 8px 0; }}
  .subtitle {{ color: #94a3b8; margin-bottom: 24px; }}
  .stats {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 24px; }}
  .stat {{
    background: #1e293b; padding: 12px 16px; border-radius: 8px;
    border: 1px solid #334155; min-width: 120px;
  }}
  .stat-label {{ color: #94a3b8; font-size: 12px; text-transform: uppercase; }}
  .stat-value {{ font-size: 24px; font-weight: bold; margin-top: 4px; }}
  .filters {{
    background: #1e293b; padding: 12px; border-radius: 8px;
    margin-bottom: 16px; display: flex; gap: 12px; flex-wrap: wrap;
  }}
  .filters select, .filters input {{
    background: #0f172a; color: #e2e8f0; border: 1px solid #334155;
    padding: 6px 10px; border-radius: 4px;
  }}
  table {{
    width: 100%; border-collapse: collapse; font-size: 13px;
    background: #1e293b; border-radius: 8px; overflow: hidden;
  }}
  th, td {{
    padding: 8px 10px; text-align: left;
    border-bottom: 1px solid #334155;
  }}
  th {{
    background: #0f172a; color: #94a3b8;
    text-transform: uppercase; font-size: 11px;
    position: sticky; top: 0; cursor: pointer;
  }}
  tr.row:hover {{ background: #334155; }}
  .time {{ white-space: nowrap; color: #94a3b8; font-size: 11px; }}
  .symbol {{ color: #fbbf24; }}
  .chip {{
    color: white; padding: 2px 8px; border-radius: 4px;
    font-size: 11px; font-weight: bold;
  }}
  a {{ color: #60a5fa; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  button {{
    background: #334155; color: #e2e8f0; border: none;
    padding: 4px 10px; border-radius: 4px; cursor: pointer;
    font-size: 12px;
  }}
  button:hover {{ background: #475569; }}
  .chart-row td {{ padding: 0; }}
  .legend {{ font-size: 12px; color: #94a3b8; margin-top: 8px; }}
</style>
</head>
<body>
  <h1>📊 Kronos Decision Report</h1>
  <div class="subtitle">All Kronos predictions logged. Generated {datetime.now(WIB).strftime('%Y-%m-%d %H:%M WIB')}</div>

  <div class="stats">
    <div class="stat"><div class="stat-label">Total Decisions</div><div class="stat-value">{total}</div></div>
    <div class="stat"><div class="stat-label">Valid (Trades)</div><div class="stat-value" style="color:#22c55e">{valid}</div></div>
    <div class="stat"><div class="stat-label">Skipped</div><div class="stat-value" style="color:#94a3b8">{skipped}</div></div>
    <div class="stat"><div class="stat-label">Closed</div><div class="stat-value">{len(closed)}</div></div>
    <div class="stat"><div class="stat-label">Wins</div><div class="stat-value" style="color:#22c55e">{wins}</div></div>
    <div class="stat"><div class="stat-label">Losses</div><div class="stat-value" style="color:#ef4444">{losses}</div></div>
    <div class="stat"><div class="stat-label">Win Rate</div><div class="stat-value" style="color:{'#22c55e' if wr >= 50 else '#ef4444'}">{wr:.1f}%</div></div>
  </div>

  <div class="filters">
    <select id="filterStatus" onchange="applyFilter()">
      <option value="">All Status</option>
      <option value="win">Win</option>
      <option value="loss">Loss</option>
      <option value="tp1_hit">TP1 Hit</option>
      <option value="open">Open</option>
      <option value="VALID">Valid</option>
      <option value="SKIP">Skip</option>
    </select>
    <select id="filterTf" onchange="applyFilter()">
      <option value="">All Timeframes</option>
      <option value="15m">15m</option>
      <option value="30m">30m</option>
      <option value="1h">1h</option>
      <option value="2h">2h</option>
    </select>
    <select id="filterMode" onchange="applyFilter()">
      <option value="">All Modes</option>
      <option value="scalping">Scalping</option>
      <option value="intraday">Intraday</option>
      <option value="swing">Swing</option>
    </select>
    <select id="filterDir" onchange="applyFilter()">
      <option value="">All Directions</option>
      <option value="long">Long</option>
      <option value="short">Short</option>
    </select>
    <input id="filterSymbol" placeholder="Symbol..." oninput="applyFilter()">
  </div>

  <table>
    <thead>
      <tr>
        <th>Time (WIB)</th>
        <th>Symbol</th>
        <th>TF</th>
        <th>Event</th>
        <th>FVG</th>
        <th>Decision</th>
        <th>Conf</th>
        <th>K Dir/TF</th>
        <th>Mode</th>
        <th>Dir</th>
        <th>Entry</th>
        <th>SL</th>
        <th>TP1</th>
        <th>TP2</th>
        <th>FVG Str</th>
        <th>RSI</th>
        <th>Status</th>
        <th>PnL%</th>
        <th>Closed</th>
        <th>Chart</th>
      </tr>
    </thead>
    <tbody id="tbody">
{table_body}
    </tbody>
  </table>

  <div class="legend">
    Click <strong>Chart</strong> to expand TradingView with RSI, StochRSI, EMA, Bollinger Bands, Volume.
    <strong>TV</strong> opens TradingView in a new tab. Times shown in WIB (UTC+7).
    Values marked with <span style="color:#94a3b8">*</span> are Kronos's predicted setup for SKIPed decisions (not executed).
  </div>

<script>
const widgetCache = {{}};
function toggleChart(id, symbol, interval) {{
  const row = document.getElementById('chart-' + id);
  if (row.style.display === 'none') {{
    row.style.display = '';
    if (!widgetCache[id]) {{
      widgetCache[id] = new TradingView.widget({{
        container_id: 'tv-host-' + id,
        symbol: 'BINANCE:' + symbol + '.P',
        interval: interval,
        timezone: 'Asia/Jakarta',
        theme: 'dark',
        style: '1',
        locale: 'en',
        toolbar_bg: '#1e1e1e',
        enable_publishing: false,
        withdateranges: true,
        hide_side_toolbar: false,
        allow_symbol_change: true,
        studies: [
          'RSI@tv-basicstudies',
          'StochasticRSI@tv-basicstudies',
          'MAExp@tv-basicstudies',
          'BB@tv-basicstudies',
          'Volume@tv-basicstudies'
        ],
        autosize: true
      }});
    }}
  }} else {{
    row.style.display = 'none';
  }}
}}

function applyFilter() {{
  const status = document.getElementById('filterStatus').value.toLowerCase();
  const tf = document.getElementById('filterTf').value;
  const mode = document.getElementById('filterMode').value;
  const dir = document.getElementById('filterDir').value;
  const symbol = document.getElementById('filterSymbol').value.toUpperCase();
  document.querySelectorAll('tr.row').forEach(row => {{
    const matchStatus = !status || row.dataset.status.toLowerCase().includes(status);
    const matchTf = !tf || row.dataset.tf === tf;
    const matchMode = !mode || row.dataset.mode === mode;
    const matchDir = !dir || row.dataset.dir === dir;
    const matchSym = !symbol || row.dataset.symbol.includes(symbol);
    const visible = matchStatus && matchTf && matchMode && matchDir && matchSym;
    row.style.display = visible ? '' : 'none';
    const next = row.nextElementSibling;
    if (next && next.classList.contains('chart-row')) {{
      if (!visible) next.style.display = 'none';
    }}
  }});
}}
</script>
</body>
</html>
"""


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "/Users/joseph/Documents/fvg-alpha-caller/kronos_report.html"
    rows = fetch()
    print(f"Fetched {len(rows)} kronos decisions")
    html = build_html(rows)
    Path(out_path).write_text(html, encoding="utf-8")
    print(f"Wrote: {out_path}")


if __name__ == "__main__":
    main()
