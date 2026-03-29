export class DataService {
  constructor(baseUrl = '') {
    this.baseUrl = baseUrl;
  }

  async fetchBars(pair, tf = '5m', startTime = null) {
    const url = `${this.baseUrl}/api/bars?pair=${encodeURIComponent(pair)}&tf=${encodeURIComponent(tf)}&start_time=${encodeURIComponent(startTime)}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Error fetching bars: ${res.status}`);
    return res.json();
  }

  async getPair() {
    const res = await fetch(`${this.baseUrl}/api/pair`);
    if (!res.ok) throw new Error(`Error fetching pair: ${res.statusText}`);
    const data = await res.json();
    return data.pair;
  }

  async fetchLines(pair) {
    const res = await fetch(`${this.baseUrl}/api/lines?pair=${encodeURIComponent(pair)}`);
    if (!res.ok) throw new Error(`Error fetching lines: ${res.status}`);
    return res.json();
  }

  async fetchTrades(pair) {
    const res = await fetch(`${this.baseUrl}/api/trades?pair=${encodeURIComponent(pair)}`);
    if (!res.ok) throw new Error(`Error fetching trades: ${res.status}`);
    return res.json();
  }

  async addLine(pair, price, creationTime = null) {
    const res = await fetch(`${this.baseUrl}/api/lines`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pair, price, creation_time: creationTime }),
    });
    if (!res.ok) {
      const txt = await res.text();
      throw new Error(`Error adding line: ${res.status} ${txt}`);
    }
    return res.json();
  }

  async deleteLine(id) {
    const res = await fetch(`${this.baseUrl}/api/lines/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    });
    if (!res.ok) {
      const txt = await res.text();
      throw new Error(`Error deleting line: ${res.status} ${txt}`);
    }
    return true;
  }
}
