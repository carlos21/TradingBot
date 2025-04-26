export class DataFetcher {
    /**
     * @param {string} baseUrl - Base API endpoint (e.g., '/api/data')
     */
    constructor(baseUrl) {
      this.baseUrl = baseUrl;
    }
  
    fetchData(ticker, timeframe, emaPeriod, rsiPeriod) {
      const url = `${this.baseUrl}/${ticker}/${timeframe}/${emaPeriod}/${rsiPeriod}`;
      return fetch(url)
        .then(response => {
          if (!response.ok) {
            throw new Error(`HTTP error! Status: ${response.status}`);
          }
          return response.json();
        });
    }
  }