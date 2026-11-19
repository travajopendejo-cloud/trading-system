"""Closed-trade statistics and the performance page."""

from __future__ import annotations

from typing import Any


def summarize(name: str, starting_equity: float, trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Win rate, profit, drawdown, and an equity curve from closed trades."""
    ordered = sorted(trades, key=lambda trade: str(trade.get("closed_at") or ""))
    pnls = [float(trade.get("pnl") or 0) for trade in ordered]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    equity = float(starting_equity)
    peak = equity
    max_drop = 0.0
    curve = [{"t": None, "equity": round(equity, 2), "pnl": 0.0, "symbol": ""}]
    for trade, pnl in zip(ordered, pnls):
        equity += pnl
        peak = max(peak, equity)
        max_drop = max(max_drop, peak - equity)
        curve.append(
            {
                "t": trade.get("closed_at"),
                "equity": round(equity, 2),
                "pnl": round(pnl, 2),
                "symbol": trade.get("symbol") or "",
            }
        )
    count = len(pnls)
    worst = sorted(ordered, key=lambda trade: float(trade.get("pnl") or 0))
    return {
        "name": name,
        "starting_equity": round(float(starting_equity), 2),
        "equity": round(equity, 2),
        "total_pnl": round(sum(pnls), 2) if pnls else 0.0,
        "trade_count": count,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / count * 100, 1) if count else None,
        "avg_pnl": round(sum(pnls) / count, 2) if count else None,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "max_drawdown_usd": round(max_drop, 2),
        "max_drawdown_pct": round(max_drop / peak * 100, 2) if peak > 0 else 0.0,
        "biggest_loss": round(min(losses), 2) if losses else 0.0,
        "worst_trades": [
            {
                "bot": trade.get("bot") or "",
                "symbol": trade.get("symbol") or "",
                "pnl": round(float(trade.get("pnl") or 0), 2),
                "closed_at": trade.get("closed_at"),
            }
            for trade in worst
            if float(trade.get("pnl") or 0) < 0
        ][:3],
        "equity_curve": curve[-300:],
    }


PERFORMANCE_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Performance · Trading System</title>
  <style>
    :root {
      --bg: #0f1117; --card: #1a1d27; --border: #2a2e3b;
      --text: #e4e6ed; --muted: #8b90a0;
      --green: #22c55e; --red: #ef4444; --accent: #6366f1;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      color: var(--text); min-height: 100vh; padding: 24px;
      background: #0b0d12;
    }
    body::before {
      content: "";
      position: fixed;
      inset: 0;
      z-index: -1;
      background-image: linear-gradient(rgba(8, 10, 16, 0.62), rgba(8, 10, 16, 0.74)), url("/static/dagobert-bg.webp");
      background-size: cover;
      background-position: center center;
      background-repeat: no-repeat;
      background-attachment: fixed;
    }
    a { color: var(--accent); text-decoration: none; }
    h1 { font-size: 1.5rem; font-weight: 600; }
    h2 { font-size: 1.05rem; margin-bottom: 4px; }
    .subtitle, .hint { color: var(--muted); font-size: 0.875rem; }
    .hint { margin: 4px 0 14px; }
    .header { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; margin-bottom: 20px; }
    .badge {
      background: rgba(99,102,241,0.2); color: #a5b4fc;
      padding: 4px 12px; border-radius: 999px; font-size: 0.75rem; font-weight: 600;
    }
    .card {
      background: rgba(18, 21, 30, 0.9); border: 1px solid var(--border);
      border-radius: 12px; padding: 18px; margin-bottom: 16px;
    }
    .stats { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); }
    .stat-label { color: var(--muted); font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; }
    .stat { font-size: 1.35rem; font-weight: 700; margin-top: 4px; }
    .green { color: var(--green); } .red { color: var(--red); }
    svg.curve { width: 100%; height: 180px; display: block; margin-top: 8px; }
    table { width: 100%; border-collapse: collapse; font-size: 0.85rem; margin-top: 8px; }
    th, td { text-align: left; padding: 8px 4px; border-bottom: 1px solid var(--border); }
    th { color: var(--muted); font-weight: 500; }
    .split { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
    .pill {
      background: rgba(12, 14, 20, 0.82);
      border: 1px solid var(--border); border-radius: 999px; padding: 6px 12px; font-size: 0.8rem;
    }
    @media (max-width: 640px) {
      body { padding: 14px; }
    }
    .section-title { margin: 22px 0 10px; }
  </style>
</head>
<body>
  <div class="header">
    <div>
      <h1>Performance</h1>
      <p class="subtitle"><a href="/">Back to the dashboard</a> · closed paper trades only</p>
    </div>
    <span class="badge">PAPER</span>
  </div>
  <div id="root"><p class="hint">Loading results…</p></div>
  <script>
    function money(n) {
      if (n == null || !isFinite(Number(n))) return '—';
      const x = Number(n);
      const body = Math.abs(x).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
      return (x < 0 ? '-$' : '$') + body;
    }
    function signed(n) {
      if (n == null || !isFinite(Number(n))) return '—';
      const x = Number(n);
      return (x > 0 ? '+' : x < 0 ? '−' : '') + money(Math.abs(x)).replace('-$', '$');
    }
    function pct(n) { return n == null ? '—' : Number(n).toFixed(1) + '%'; }
    function cls(n) { return Number(n) > 0 ? 'green' : Number(n) < 0 ? 'red' : ''; }

    function chart(points) {
      const vals = (points || []).map(p => Number(p.equity));
      if (!vals.length) return '';
      const w = 640, h = 180, pad = 18;
      const min = Math.min(...vals), max = Math.max(...vals);
      const span = (max - min) || 1;
      const coords = vals.map((v, i) => {
        const x = vals.length === 1 ? w / 2 : pad + i * (w - pad * 2) / (vals.length - 1);
        const y = pad + (1 - (v - min) / span) * (h - pad * 2);
        return x.toFixed(1) + ',' + y.toFixed(1);
      }).join(' ');
      const up = vals[vals.length - 1] >= vals[0];
      const color = up ? '#22c55e' : '#ef4444';
      return `<svg class="curve" viewBox="0 0 ${w} ${h}" role="img" aria-label="Equity curve">
        <line x1="${pad}" y1="${h - pad}" x2="${w - pad}" y2="${h - pad}" stroke="#2a2e3b" />
        <polyline fill="none" stroke="${color}" stroke-width="2.5" points="${coords}" />
      </svg>
      <p class="hint">Starts at ${money(vals[0])} and is ${money(vals[vals.length - 1])} after closed trades.</p>`;
    }

    function stat(label, value, klass) {
      return `<div><div class="stat-label">${label}</div><div class="stat ${klass || ''}">${value}</div></div>`;
    }

    function block(report) {
      const worst = report.worst_trades || [];
      const worstTable = worst.length
        ? `<table><thead><tr><th>Symbol</th><th>Bot</th><th>Loss</th></tr></thead><tbody>
            ${worst.map(t => `<tr><td>${t.symbol || '—'}</td><td>${t.bot || '—'}</td><td class="red">${signed(t.pnl)}</td></tr>`).join('')}
           </tbody></table>`
        : '<p class="hint">No losing trades yet.</p>';
      const share = report.allocation_pct == null ? '' : `<p class="hint">Paper slice: ${Number(report.allocation_pct).toFixed(1)}% · ${money(report.starting_equity)}</p>`;
      return `<article class="card">
        <h2>${report.name}</h2>
        ${share}
        <div class="stats">
          ${stat('Win rate', pct(report.win_rate_pct))}
          ${stat('Total profit / loss', signed(report.total_pnl), cls(report.total_pnl))}
          ${stat('Average trade', signed(report.avg_pnl), cls(report.avg_pnl))}
          ${stat('Trades', report.trade_count)}
          ${stat('Average win', signed(report.avg_win), 'green')}
          ${stat('Average loss', signed(report.avg_loss), 'red')}
          ${stat('Largest drop', money(report.max_drawdown_usd) + ' (' + pct(report.max_drawdown_pct) + ')', 'red')}
          ${stat('Largest single loss', signed(report.biggest_loss), 'red')}
        </div>
        <p class="hint" style="margin-top:14px">Equity over time. Each step is one closed trade.</p>
        ${chart(report.equity_curve)}
        <p class="hint" style="margin-top:10px">Biggest losses</p>
        ${worstTable}
      </article>`;
    }

    function render(data) {
      const alloc = data.allocation || [];
      const pills = alloc.map(a => `<span class="pill"><strong>${a.name}</strong> ${Number(a.pct).toFixed(1)}% · ${money(a.usd)}</span>`).join('');
      document.getElementById('root').innerHTML = `
        <section class="card">
          <h2>How the money is split</h2>
          <p class="hint">${data.note || ''}</p>
          <div class="split">${pills}</div>
          <p class="hint" style="margin-top:12px">To change the meme sniper's share, edit <code>portfolio_share</code> in <code>config/global.yaml</code>. Use 0.10 to 0.30 (10% to 30%). The default is 0.20. The other bots split whatever is left. Then restart the app.</p>
        </section>
        <h2 class="section-title">Whole portfolio</h2>
        ${block(data.portfolio)}
        <h2 class="section-title">Each bot</h2>
        ${(data.bots || []).map(block).join('')}`;
    }

    async function load() {
      try {
        const res = await fetch('/api/performance');
        render(await res.json());
      } catch (err) {
        document.getElementById('root').innerHTML = '<p class="hint">Could not load performance.</p>';
      }
    }
    load();
    setInterval(load, 5000);
  </script>
</body>
</html>
"""
