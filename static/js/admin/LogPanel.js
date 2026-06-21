/**
 * System Log Panel
 * Streams real-time logs from the server via Socket.IO
 */
export class LogPanel {
  constructor(socket) {
    this.socket = socket;
    this.entries = [];
    this.paused = false;
    this.maxEntries = 500;

    this.container = document.getElementById('log-entries');
    this.levelFilter = document.getElementById('log-level-filter');
    this.sourceFilter = document.getElementById('log-source-filter');
    this.searchFilter = document.getElementById('log-search-filter');
    this.clearBtn = document.getElementById('log-clear-btn');
    this.pauseBtn = document.getElementById('log-pause-btn');
    this.statusEl = document.getElementById('log-connection-status');

    this._bindEvents();
  }

  _bindEvents() {
    this.clearBtn?.addEventListener('click', () => this.clear());
    this.pauseBtn?.addEventListener('click', () => this.togglePause());

    [this.levelFilter, this.sourceFilter, this.searchFilter].forEach(el => {
      el?.addEventListener('input', () => this.render());
    });

    this.socket.on('system_log', (data) => this.onLogEntry(data));
    this.socket.on('connect', () => this._setStatus('Connected', 'text-emerald-400'));
    this.socket.on('disconnect', () => this._setStatus('Disconnected', 'text-rose-500'));
  }

  _setStatus(text, colorClass) {
    if (this.statusEl) {
      this.statusEl.textContent = text;
      this.statusEl.className = `text-xs ${colorClass}`;
    }
  }

  onLogEntry(data) {
    if (this.paused) return;

    const entry = {
      time: data.time || Date.now() / 1000,
      level: data.level || 'INFO',
      source: data.source || 'system',
      message: data.message || '',
    };

    this.entries.push(entry);
    if (this.entries.length > this.maxEntries) {
      this.entries.shift();
    }

    // Use incremental append when no filters are active to avoid full re-render
    if (this.container && !this._hasActiveFilters() && this._matchesFilters(entry)) {
      // Remove the "no logs" placeholder if present
      const placeholder = this.container.querySelector('.text-center');
      if (placeholder) placeholder.remove();

      this._appendEntry(entry);

      // Remove oldest DOM node if over display limit
      while (this.container.children.length > 200) {
        this.container.removeChild(this.container.firstChild);
      }

      this.container.scrollTop = this.container.scrollHeight;
    } else {
      this.render();
    }
  }

  _hasActiveFilters() {
    return !!(this.levelFilter?.value || this.sourceFilter?.value || this.searchFilter?.value);
  }

  clear() {
    this.entries = [];
    this.render();
  }

  togglePause() {
    this.paused = !this.paused;
    if (this.pauseBtn) {
      this.pauseBtn.textContent = this.paused ? 'Resume' : 'Pause';
      this.pauseBtn.classList.toggle('bg-amber-600', this.paused);
    }
  }

  _matchesFilters(entry) {
    const level = this.levelFilter?.value || '';
    const source = (this.sourceFilter?.value || '').toLowerCase();
    const search = (this.searchFilter?.value || '').toLowerCase();

    if (level && entry.level !== level) return false;
    if (source && !entry.source.toLowerCase().includes(source)) return false;
    if (search && !entry.message.toLowerCase().includes(search)) return false;
    return true;
  }

  _levelColor(level) {
    switch (level) {
      case 'ERROR': return 'text-rose-500 border-l-rose-500';
      case 'WARN': return 'text-amber-400 border-l-amber-400';
      case 'INFO': return 'text-accent-400 border-l-accent-400';
      default: return 'text-slate-400 border-l-slate-500';
    }
  }

  _levelBg(level) {
    switch (level) {
      case 'ERROR': return 'bg-rose-500/5';
      case 'WARN': return 'bg-amber-400/5';
      case 'INFO': return 'bg-accent-500/5';
      default: return '';
    }
  }

  render() {
    if (!this.container) return;

    const filtered = this.entries.filter(e => this._matchesFilters(e));

    if (filtered.length === 0) {
      this.container.innerHTML = '<div class="text-slate-500 text-center py-8 text-sm">No logs match filters</div>';
      return;
    }

    this.container.innerHTML = filtered.slice(-200).map(entry => {
      const timeStr = new Date(entry.time * 1000).toLocaleTimeString('en-US', {
        hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit',
      });
      const colorClass = this._levelColor(entry.level);
      const bgClass = this._levelBg(entry.level);

      return `
        <div class="log-entry text-xs font-mono border-l-2 ${colorClass} ${bgClass} rounded px-2 py-1.5">
          <span class="text-slate-500">${timeStr}</span>
          <span class="font-bold ml-2">${entry.level}</span>
          <span class="text-slate-400 ml-2">[${entry.source}]</span>
          <span class="ml-2 text-slate-200">${this._escapeHtml(entry.message)}</span>
        </div>
      `;
    }).join('');

    // Auto-scroll to bottom
    this.container.scrollTop = this.container.scrollHeight;
  }

  _appendEntry(entry) {
    const timeStr = new Date(entry.time * 1000).toLocaleTimeString('en-US', {
      hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
    const colorClass = this._levelColor(entry.level);
    const bgClass = this._levelBg(entry.level);

    const div = document.createElement('div');
    div.className = `log-entry text-xs font-mono border-l-2 ${colorClass} ${bgClass} rounded px-2 py-1.5`;
    div.innerHTML =
      `<span class="text-slate-500">${timeStr}</span>` +
      `<span class="font-bold ml-2">${entry.level}</span>` +
      `<span class="text-slate-400 ml-2">[${entry.source}]</span>` +
      `<span class="ml-2 text-slate-200">${this._escapeHtml(entry.message)}</span>`;
    this.container.appendChild(div);
  }

  _escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }
}
