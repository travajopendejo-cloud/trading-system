"""Simple web dashboard with bot controls + /metrics (stdlib only)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable
from urllib.parse import urlparse

import logging
logger = logging.getLogger(__name__)

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Multi-Bot Trading Dashboard</title>
  <style>
    :root {
      --bg: #0f1117; --card: #1a1d27; --border: #2a2e3b;
      --text: #e4e6ed; --muted: #8b90a0;
      --green: #22c55e; --red: #ef4444; --blue: #3b82f6;
      --yellow: #eab308; --accent: #6366f1;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: var(--bg); color: var(--text); min-height: 100vh; padding: 24px;
    }
    h1 { font-size: 1.5rem; font-weight: 600; margin-bottom: 4px; }
    .subtitle { color: var(--muted); font-size: 0.875rem; margin-bottom: 24px; }
    .grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }
    .card {
      background: var(--card); border: 1px solid var(--border);
      border-radius: 12px; padding: 20px;
    }
    .card h2 {
      font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em;
      color: var(--muted); margin-bottom: 12px;
    }
    .stat { font-size: 1.75rem; font-weight: 700; }
    .stat.green { color: var(--green); }
    .stat.red { color: var(--red); }
    .stat.yellow { color: var(--yellow); }
    .bot-row {
      display: flex; align-items: center; justify-content: space-between;
      padding: 12px 0; border-bottom: 1px solid var(--border);
    }
    .bot-row:last-child { border-bottom: none; }
    .bot-name { font-weight: 600; }
    .badge {
      display: inline-block; padding: 2px 10px; border-radius: 999px;
      font-size: 0.75rem; font-weight: 600;
    }
    .badge.running { background: rgba(34,197,94,0.15); color: var(--green); }
    .badge.stopped { background: rgba(139,144,160,0.15); color: var(--muted); }
    .badge.error { background: rgba(239,68,68,0.15); color: var(--red); }
    .btn {
      border: none; border-radius: 8px; padding: 6px 14px; font-size: 0.8rem;
      font-weight: 600; cursor: pointer; transition: opacity 0.15s;
    }
    .btn:hover { opacity: 0.85; }
    .btn:disabled { opacity: 0.4; cursor: not-allowed; }
    .btn-start { background: var(--green); color: #fff; }
    .btn-stop { background: var(--red); color: #fff; }
    .btn-all { background: var(--accent); color: #fff; margin-right: 8px; }
    .controls { margin-top: 16px; display: flex; gap: 8px; flex-wrap: wrap; }
    .positions table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
    .positions th, .positions td {
      text-align: left; padding: 8px 4px; border-bottom: 1px solid var(--border);
    }
    .positions th { color: var(--muted); font-weight: 500; }
    .refresh { color: var(--muted); font-size: 0.75rem; margin-top: 16px; }
    .header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 8px; }
    .mode-badge {
      background: rgba(99,102,241,0.2); color: var(--accent);
      padding: 4px 12px; border-radius: 999px; font-size: 0.75rem; font-weight: 600;
    }
    a.metrics-link { color: var(--accent); font-size: 0.8rem; }
  </style>
</head>
<body>
  <div class="header">
    <div>
      <h1>Multi-Bot Trading System</h1>
      <p class="subtitle">Live monitoring & controls · <a class="metrics-link" href="/metrics" target="_blank">Prometheus metrics</a></p>
    </div>
    <span class="mode-badge" id="mode">—</span>
  </div>

  <div class="grid" style="margin-bottom: 20px;">
    <div class="card"><h2>Equity</h2><div class="stat" id="equity">—</div></div>
    <div class="card"><h2>Drawdown</h2><div class="stat" id="drawdown">—</div></div>
    <div class="card"><h2>Open Positions</h2><div class="stat" id="positions-count">—</div></div>
    <div class="card"><h2>Daily PnL</h2><div class="stat" id="daily-pnl">—</div></div>
  </div>

  <div class="grid">
    <div class="card">
      <h2>Bots</h2>
      <div id="bots-list"></div>
      <div class="controls">
        <button class="btn btn-all" onclick="controlAll('start')">Start All</button>
        <button class="btn btn-stop" onclick="controlAll('stop')">Stop All</button>
      </div>
    </div>

    <div class="card positions">
      <h2>Open Positions</h2>
      <table>
        <thead><tr><th>Bot</th><th>Symbol</th><th>Side</th><th>Size</th><th>Entry</th></tr></thead>
        <tbody id="positions-body">
          <tr><td colspan="5" style="color:var(--muted)">No open positions</td></tr>
        </tbody>
      </table>
    </div>

    <div class="card positions" style="grid-column: 1 / -1;">
      <h2>Trade History (Last 15)</h2>
      <table>
        <thead>
          <tr>
            <th>Bot</th>
            <th>Symbol</th>
            <th>Side</th>
            <th>Size</th>
            <th>Entry</th>
            <th>Exit</th>
            <th>PnL</th>
            <th>Closed</th>
          </tr>
        </thead>
        <tbody id="trades-body">
          <tr><td colspan="8" style="color:var(--muted)">No closed trades yet</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <p class="refresh">Auto-refreshes every 3s · <span id="last-update"></span></p>

  <script>
    async function fetchStatus() {
      try {
        const res = await fetch('/api/status');
        const data = await res.json();
        render(data);
      } catch (e) {
        console.error('Status fetch error:', e);
      }
    }

    function render(data) {
      document.getElementById('mode').textContent = (data.mode || '—').toUpperCase();

      const equity = data.equity ?? 0;
      const equityEl = document.getElementById('equity');
      equityEl.textContent = fmt(equity);
      equityEl.className = 'stat' + (equity >= (data.initial_equity || 10000) ? ' green' : ' red');

      const dd = data.drawdown_pct ?? 0;
      const ddEl = document.getElementById('drawdown');
      ddEl.textContent = dd.toFixed(2) + '%';
