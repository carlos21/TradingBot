//+------------------------------------------------------------------+
//|                                Application/HistoryProvider.mqh   |
//|  IHistoryProvider implementation. Sends historical 1m bars.      |
//+------------------------------------------------------------------+
#property strict

#include "../Domain/Contracts.mqh"
#include "../Domain/MessageTypes.mqh"
#include "../Domain/ValueObjects.mqh"
#include <JSON/JSON.mqh>

//+------------------------------------------------------------------+
//| HistoryProvider — sends historical bars in batches               |
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
   void SendHistory() override
   {
      if(m_network == NULL) return;

      datetime end = TimeCurrent();
      datetime start = end - m_config.historyDays * 86400;

      MqlRates rates[];
      int total = CopyRates(m_symbol, PERIOD_M1, start, end, rates);
      if(total <= 0)
      {
         if(m_logger != NULL)
            m_logger.Warning("HistoryProvider: CopyRates returned " + IntegerToString(total));
         m_network.SendHistoryEnd(m_symbol);
         return;
      }

      if(m_logger != NULL)
         m_logger.Info("HistoryProvider: sending " + IntegerToString(total) + " bars (" + IntegerToString(m_config.historyDays) + " days)");

      // Send in batches
      int batchSize = (m_config.batchSize > 0) ? m_config.batchSize : 500;
      for(int i = 0; i < total; i += batchSize)
      {
         int endIdx = MathMin(i + batchSize, total);
         JSONValue *barsArray = new JSONValue(JSON_ARRAY);

         for(int j = i; j < endIdx; j++)
         {
            JSONValue *bar = new JSONValue(JSON_OBJECT);
            bar["time"]   = new JSONValue((long)rates[j].time);
            bar["open"]   = new JSONValue(rates[j].open);
            bar["high"]   = new JSONValue(rates[j].high);
            bar["low"]    = new JSONValue(rates[j].low);
            bar["close"]  = new JSONValue(rates[j].close);
            bar["volume"] = new JSONValue((long)rates[j].tick_volume);
            bar["pair"]   = new JSONValue(m_symbol);

            barsArray.Add(bar);
            // NOTE: Add() takes ownership — do NOT delete bar
         }

         m_network.SendHistoryBatch(m_symbol, barsArray, m_config.historyDays);
         // NOTE: SendHistoryBatch puts barsArray into a payload tree which is then deleted.
         // Do NOT delete barsArray here.

         // Small yield to prevent blocking OnTick for too long
         if(i + m_config.batchSize < total)
            Sleep(5);
      }

      m_network.SendHistoryEnd(m_symbol);

      if(m_logger != NULL)
         m_logger.Info("HistoryProvider: history send complete");
   }
};
