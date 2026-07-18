/**
 * System Log Panel
 * Loads historical logs from the server and streams real-time logs via Socket.IO
 */
export class LogPanel {
  constructor(socket, api, pair = null) {
    this.socket = socket;
    this.api = api;
    this.selectedPair = pair;
    this.entries = [];
    this.sources = new Set();
    this.paused = false;
    this.maxEntries = 500;
    this.limit = 200;
    this.offset = 0;
    this.hasMore = false;
    this.loading = false;

    this.container = document.getElementById('log-entries');
    this.levelFilter = document.getElementById('log-level-filter');
    this.sourceFilter = document.getElementById('log-source-filter');
    this.searchFilter = document.getElementById('log-search-filter');
    this.clearBtn = document.getElementById('log-clear-btn');
    this.pauseBtn = document.getElementById('log-pause-btn');
    this.loadMoreBtn = document.getElementById('log-load-more-btn');
    this.statusEl = document.getElementById('log-connection-status');

    this._bindEvents();
    this._loadInitial();
  }

  _bindEvents() {
    this.clearBtn?.addEventListener('click', () => this.clear());
    this.pauseBtn?.addEventListener('click', () => this.togglePause());
    this.loadMoreBtn?.addEventListener('click', () => this.loadMore());

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

  setPair(pair) {
    this.selectedPair = pair;
    this.offset = 0;
    this.hasMore = false;
    this.entries = [];
    this._fetchPage(0);
  }

  async _loadInitial() {
    await this._fetchPage(0);
  }

  async _fetchPage(offset) {
    if (this.loading || !this.api) return;
    this.loading = true;
    this.loadMoreBtn?.classList.add('opacity-50', 'cursor-not-allowed');

    try {
      const data = await this.api.getRecentLogs(this.selectedPair, this.limit, offset);
      const logs = data.logs || [];
      const sources = data.sources || [];
      this.hasMore = data.has_more || false;

      sources.forEach(s => this.sources.add(s));
      this._updateSourceDropdown();

      if (offset === 0) {
        this.entries = logs;
      } else {
        // Append older entries to the bottom (newest are already at the top)
        this.entries = this.entries.concat(logs);
      }

      this.offset = offset + logs.length;
      this.render();

      // On initial load, scroll to the top so the newest log is visible
      if (offset === 0 && !this._hasActiveFilters()) {
        this.container.scrollTop = 0;
      }
    } catch (error) {
      console.error('[LogPanel] Failed to load logs:', error);
    } finally {
      this.loading = false;
      this.loadMoreBtn?.classList.remove('opacity-50', 'cursor-not-allowed');
      this._updateLoadMoreVisibility();
    }
  }

  async loadMore() {
    if (!this.hasMore || this.loading) return;
    await this._fetchPage(this.offset);
  }

  _updateLoadMoreVisibility() {
    if (!this.loadMoreBtn) return;
    if (this.hasMore) {
      this.loadMoreBtn.classList.remove('hidden');
    } else {
      this.loadMoreBtn.classList.add('hidden');
    }
  }

  _updateSourceDropdown() {
    if (!this.sourceFilter) return;

    const currentValue = this.sourceFilter.value;
    const sortedSources = Array.from(this.sources).sort();

    // Preserve the "All" option
    this.sourceFilter.innerHTML = '<option value="">All</option>';

    sortedSources.forEach(source => {
      const option = document.createElement('option');
      option.value = source;
      option.textContent = source;
      this.sourceFilter.appendChild(option);
    });

    // Restore previous selection if still valid, otherwise keep "All"
    if (currentValue && sortedSources.includes(currentValue)) {
      this.sourceFilter.value = currentValue;
    }
  }

  onLogEntry(data) {
    if (this.paused) return;

    const entry = {
      time: data.time || Date.now() / 1000,
      level: data.level || 'INFO',
      source: data.source || 'server',
      message: data.message || '',
    };

    // Newest entries live at the front of the array
    this.entries.unshift(entry);
    if (this.entries.length > this.maxEntries) {
      this.entries.pop();
    }

    if (entry.source) {
      this.sources.add(entry.source);
      this._updateSourceDropdown();
    }

    // Use incremental prepend when no filters are active to avoid full re-render
    if (this.container && !this._hasActiveFilters() && this._matchesFilters(entry)) {
      this._removePlaceholder();
      const wasAtTop = this.container.scrollTop === 0;
      this._prependEntry(entry);

      // Remove oldest DOM node if over display limit
      while (this.container.children.length > 200) {
        this.container.removeChild(this.container.lastChild);
      }

      // Keep the view pinned to the top if the user was already there
      if (wasAtTop) {
        this.container.scrollTop = 0;
      }
    } else {
      this.render();
    }
  }

  _removePlaceholder() {
    const placeholder = this.container.querySelector('.text-center');
    if (placeholder) placeholder.remove();
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
    const source = this.sourceFilter?.value || '';
    const search = (this.searchFilter?.value || '').toLowerCase();

    if (level && entry.level !== level) return false;
    if (source && entry.source !== source) return false;
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
      const message = this._hasActiveFilters()
        ? 'No logs match filters'
        : 'No logs available';
      this.container.innerHTML = `<div class="text-slate-500 text-center py-8 text-sm">${message}</div>`;
      return;
    }

    // Show all loaded entries (newest-first). Real-time updates keep the DOM
    // bounded separately, so historical logs loaded via "Load more" remain visible.
    this.container.innerHTML = filtered.map(entry => this._entryHtml(entry)).join('');
  }

  _prependEntry(entry) {
    const div = document.createElement('div');
    div.className = this._entryClassName(entry);
    div.innerHTML = this._entryInnerHtml(entry);
    this.container.insertBefore(div, this.container.firstChild);
  }

  _entryClassName(entry) {
    const colorClass = this._levelColor(entry.level);
    const bgClass = this._levelBg(entry.level);
    return `log-entry text-xs font-mono border-l-2 ${colorClass} ${bgClass} rounded px-2 py-1.5`;
  }

  _entryHtml(entry) {
    return `
      <div class="${this._entryClassName(entry)}">
        ${this._entryInnerHtml(entry)}
      </div>
    `;
  }

  _entryInnerHtml(entry) {
    const timeStr = new Date(entry.time * 1000).toLocaleTimeString('en-US', {
      hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit',
    });

    return `
      <span class="text-slate-500">${timeStr}</span>
      <span class="font-bold ml-2">${entry.level}</span>
      <span class="text-slate-400 ml-2">[${this._escapeHtml(entry.source)}]</span>
      <span class="ml-2 text-slate-200">${this._escapeHtml(entry.message)}</span>
    `;
  }

  _escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }
}
