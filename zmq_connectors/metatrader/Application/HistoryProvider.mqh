//+------------------------------------------------------------------+
//|                                Application/HistoryProvider.mqh   |
//|  IHistoryProvider implementation. Sends historical 1m bars.      |
//+------------------------------------------------------------------+
#property strict

#include "../Domain/Contracts.mqh"
#include "../Domain/MessageTypes.mqh"
#include "../Domain/ValueObjects.mqh"
#include "../Domain/PlatformApi.mqh"

//+------------------------------------------------------------------+
//| HistoryProvider — sends historical bars in batches               |
//|  Uses raw JSON string building to bypass MQL5 JSON library       |
//|  stack-overflow issues with large object trees.                  |
//|  Protocol: refresh_start first (Python clears recent bars on     |
//|  it), then history_batch chunks, then history_end.               |
//+------------------------------------------------------------------+
class HistoryProvider : public IHistoryProvider
{
private:
   IZmqNetwork        *m_network;
   ILogger            *m_logger;
   ZmqConfiguration   *m_config;
   IMarketDataApi     *m_marketData;
   ITimeApi           *m_timeApi;
   string              m_symbol;

public:
   HistoryProvider(IZmqNetwork *network, ILogger *logger, ZmqConfiguration *config, string symbol, IMarketDataApi *marketData, ITimeApi *timeApi)
   {
      m_network = network;
      m_logger = logger;
      m_config = config;
      m_marketData = marketData;
      m_timeApi = timeApi;
      m_symbol = symbol;
   }

   ~HistoryProvider() {}

   //--- IHistoryProvider implementation
   void SendHistory(int days = 0, string symbol = "") override
   {
      if(m_network == NULL) return;

      string sym = (StringLen(symbol) > 0) ? symbol : m_symbol;
      int historyDays = (days > 0) ? days : m_config.historyDays;
      if(historyDays <= 0) historyDays = 1;

      // Protocol: refresh_start FIRST — Python clears recent bars on it
      m_network.SendRefreshStart(sym);

      datetime end = m_timeApi.Now();
      datetime start = end - historyDays * 86400;

      int total = m_marketData.BarsCount(sym, start, end);
      if(total <= 0)
      {
         if(m_logger != NULL)
            m_logger.Warning("HistoryProvider: Bars() returned " + IntegerToString(total));
         m_network.SendHistoryEnd(sym);
         return;
      }

      if(m_logger != NULL)
         m_logger.Info("HistoryProvider: sending " + IntegerToString(total) + " bars (" + IntegerToString(historyDays) + " days) for " + sym);

      // Batch size raised toward 500 — the manual string builder is
      // heap-based so bigger batches are safe. 5ms sleep between batches.
      int batchSize = m_config.batchSize;
      if(batchSize < 10)  batchSize = 10;
      if(batchSize > 500) batchSize = 500;

      MqlRates rates[];
      ArrayResize(rates, batchSize);
      int totalSent = 0;

      for(int pos = total - 1; pos >= 0; )
      {
         int count = MathMin(batchSize, pos + 1);
         int startPos = pos - count + 1;

         int copied = m_marketData.CopyM1Rates(sym, startPos, count, rates);
         if(copied <= 0) break;

         // Build bars JSON manually — bypasses MQL5 JSON library completely
         string barsJson = "";
         for(int j = 0; j < copied; j++)
         {
            if(j > 0) barsJson += ",";
            string sOpen  = DoubleToString(rates[j].open, 5);
            string sHigh  = DoubleToString(rates[j].high, 5);
            string sLow   = DoubleToString(rates[j].low, 5);
            string sClose = DoubleToString(rates[j].close, 5);
            // Ensure dot decimal separator regardless of locale
            StringReplace(sOpen,  ",", ".");
            StringReplace(sHigh,  ",", ".");
            StringReplace(sLow,   ",", ".");
            StringReplace(sClose, ",", ".");
            barsJson += "{\"time\":" + IntegerToString((long)rates[j].time)
                      + ",\"open\":" + sOpen
                      + ",\"high\":" + sHigh
                      + ",\"low\":" + sLow
                      + ",\"close\":" + sClose
                      + ",\"volume\":" + IntegerToString((long)rates[j].tick_volume)
                      + ",\"pair\":\"" + sym + "\"}";
         }

         m_network.SendRawHistoryBatch(sym, barsJson, historyDays);
         totalSent += copied;
         pos -= count;

         Sleep(5);
      }

      m_network.SendHistoryEnd(sym);

      if(m_logger != NULL)
         m_logger.Info("HistoryProvider: history send complete, " + IntegerToString(totalSent) + " bars sent");
   }
};
