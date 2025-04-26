export class WatchListViewer {
    /**
     * @param {HTMLElement} watchlistElement - DOM element to display the watchlist items
     * @param {string} apiUrl - URL to fetch the watchlist symbols (e.g., '/api/symbols')
     */
    constructor(watchlistElement, apiUrl) {
      this.watchlistElement = watchlistElement;
      this.apiUrl = apiUrl;
    }
  
    loadWatchlist() {
      return fetch(this.apiUrl)
        .then(response => {
          if (!response.ok) {
            throw new Error('Failed to load watchlist');
          }
          return response.json();
        })
        .then(symbols => {
          this.watchlistElement.innerHTML = '';
          symbols.forEach(symbol => {
            const item = document.createElement('div');
            item.className = 'watchlist-item';
            item.innerText = symbol;
            // Trigger a custom event on click so that external code can subscribe
            item.addEventListener('click', () => {
              const event = new CustomEvent('watchlistSymbolSelected', { detail: symbol });
              this.watchlistElement.dispatchEvent(event);
            });
            this.watchlistElement.appendChild(item);
          });
          return symbols;
        });
    }
  }