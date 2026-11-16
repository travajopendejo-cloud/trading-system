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
      // Mode
      document.getElementById('mode').textContent = (data.mode || '—').toUpperCase();

      // Equity
      const equity = data.equity ?? 0;
      const equityEl = document.getElementById('equity');
      equityEl.textContent = fmt(equity);
      equityEl.className = 'stat' + (equity >= (data.initial_equity || 10000) ? ' green' : ' red');

      // Drawdown
      const dd = data.drawdown_pct ?? 0;
      const ddEl = document.getElementById('drawdown');
      ddEl.textContent = dd.toFixed(2) + '%';
      ddEl.className = 'stat' + (dd > 5 ? ' red' : dd > 2 ? ' yellow' : '');

      // Open Positions count
      document.getElementById('positions-count').textContent = data.open_positions ?? 0;

      // Daily PnL
      const pnl = data.daily_pnl ?? 0;
      const pnlEl = document.getElementById('daily-pnl');
      pnlEl.textContent = (pnl >= 0 ? '+' : '') + fmt(pnl);
      pnlEl.className = 'stat' + (pnl >= 0 ? ' green' : ' red');

      // Bots
      const botsEl = document.getElementById('bots-list');
      botsEl.innerHTML = '';
      (data.bots || []).forEach(bot => {
        const statusClass = bot.status === 'running' ? 'running' : bot.status === 'error' ? 'error' : 'stopped';
        const row = document.createElement('div');
        row.className = 'bot-row';
        row.innerHTML = `
          <div>
            <div class="bot-name">${bot.name}</div>
            <span class="badge ${statusClass}">${bot.status}</span>
            <span style="color:var(--muted);font-size:0.75rem;margin-left:8px">${bot.symbols || ''}</span>
          </div>
          <div>
            ${bot.status === 'running'
              ? `<button class="btn btn-stop" onclick="control('${bot.name}','stop')">Stop</button>`
              : `<button class="btn btn-start" onclick="control('${bot.name}','start')">Start</button>`}
          </div>`;
        botsEl.appendChild(row);
      });

      // Positions table
      const tbody = document.getElementById('positions-body');
      const positions = data.positions || [];
      tbody.innerHTML = positions.length === 0
        ? '<tr><td colspan="5" style="color:var(--muted)">No open positions</td></tr>'
        : positions.map(p => `
          <tr>
            <td>${p.bot}</td>
            <td>${p.symbol}</td>
            <td style="color:${p.side === 'long' ? 'var(--green)' : 'var(--red)'}">${p.side}</td>
            <td>${Number(p.amount).toFixed(5)}</td>
            <td>${Number(p.entry_price).toFixed(2)}</td>
          </tr>`).join('');

      document.getElementById('last-update').textContent = new Date().toLocaleTimeString();
    }

    async function control(bot, action) {
      try {
        await fetch('/api/control', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({bot, action})
        });
        setTimeout(fetchStatus, 500);
      } catch (e) {
        console.error(e);
      }
    }

    async function controlAll(action) {
      try {
        await fetch('/api/control', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({bot: 'all', action})
        });
        setTimeout(fetchStatus, 500);
      } catch (e) {
        console.error(e);
      }
    }

    function fmt(n) {
      if (n == null || isNaN(n)) return '—';
      return Number(n).toLocaleString(undefined, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
      });
    }

    // Start
    fetchStatus();
    setInterval(fetchStatus, 3000);
  </script>
