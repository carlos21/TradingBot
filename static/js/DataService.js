export class DataService {
  constructor(baseUrl = '') {
    this.baseUrl = baseUrl;
  }

  // Fetch the last N bars (default 10k)
  fetchBars(pair, tf = '5m', startTime = null) {
    const url = `/api/bars?pair=${encodeURIComponent(pair)}&tf=${encodeURIComponent(tf)}&start_time=${encodeURIComponent(startTime)}`;
    return fetch(url).then(res => {
      if (!res.ok) throw new Error(`Error fetching bars: ${res.status}`);
      return res.json();
    });
  }

  // List all pinned lines
  async fetchLines() {
    const resp = await fetch(`${this.baseUrl}/api/lines`);
    if (!resp.ok) throw new Error(`Error fetching lines: ${resp.statusText}`);
    // returns [{ id, pair, price, creation_date }, …]
    return resp.json();
  }

  // Add a new line with `pair` and `price`
  async addLine(pair, price) {
    const resp = await fetch(`${this.baseUrl}/api/lines`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pair, price }),
    });
    if (!resp.ok) {
      const txt = await resp.text();
      throw new Error(`Error adding line: ${resp.status} ${txt}`);
    }
    // returns the created LineData: { id, pair, price, creation_date }
    return resp.json();
  }

  // Delete a line by its UUID
  async deleteLine(id) {
    const resp = await fetch(`${this.baseUrl}/api/lines/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    });
    if (!resp.ok) {
      const txt = await resp.text();
      throw new Error(`Error deleting line: ${resp.status} ${txt}`);
    }
    return true;
  }
}