//+------------------------------------------------------------------+
//|                                Application/HistoryProvider.mqh   |
//|  IHistoryProvider implementation. Sends historical 1m bars.      |
//+------------------------------------------------------------------+
#property strict

#include "../Domain/Contracts.mqh"
#include "../Domain/MessageTypes.mqh"
#include "../Domain/ValueObjects.mqh"

//+------------------------------------------------------------------+
//| HistoryProvider — sends historical bars in batches               |
//|  Uses raw JSON string building to bypass MQL5 JSON library       |
//|  stack-overflow issues with large object trees.                  |
//+------------------------------------------------------------------+
class HistoryProvider : public IHistoryProvider
{
private:
   IZmqNetwork        *m_network;
   ILogger            *m_logger;
   ZmqConfiguration   *m_config;
   string              m_symbol;

public:
   HistoryProvider(IZmqNetwork *network, ILogger *logger, ZmqConfiguration *config, string symbol)
   {
      m_network = network;
      m_logger = logger;
      m_config = config;
      m_symbol = symbol;
   }

   ~HistoryProvider() {}

   //--- IHistoryProvider implementation
   void SendHistory(int days = 0) override
   {
      if(m_network == NULL) return;

      int historyDays = (days > 0) ? days : m_config.historyDays;
      if(historyDays <= 0) historyDays = 1;

      datetime end = TimeCurrent();
      datetime start = end - historyDays * 86400;

      int total = Bars(m_symbol, PERIOD_M1, start, end);
      if(total <= 0)
      {
         if(m_logger != NULL)
            m_logger.Warning("HistoryProvider: Bars() returned " + IntegerToString(total));
         m_network.SendHistoryEnd(m_symbol);
         return;
      }

      if(m_logger != NULL)
         m_logger.Info("HistoryProvider: sending " + IntegerToString(total) + " bars (" + IntegerToString(historyDays) + " days)");

      // Use a small static array to avoid stack overflow.
      MqlRates rates[10];
      const int BATCH = 10;
      int totalSent = 0;

      for(int pos = total - 1; pos >= 0; )
      {
         int count = MathMin(BATCH, pos + 1);
         int startPos = pos - count + 1;

         int copied = CopyRates(m_symbol, PERIOD_M1, startPos, count, rates);
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
                      + ",\"pair\":\"" + m_symbol + "\"}";
         }

         m_network.SendRawHistoryBatch(m_symbol, barsJson, historyDays);
         totalSent += copied;
         pos -= count;

         Sleep(5);
      }

      m_network.SendHistoryEnd(m_symbol);

      if(m_logger != NULL)
         m_logger.Info("HistoryProvider: history send complete, " + IntegerToString(totalSent) + " bars sent");
   }
};
