//+------------------------------------------------------------------+
//|                                  Application/BrokerSync.mqh      |
//|  Broker state synchronization: position restore/report on        |
//|  connect and deal-history fill detection on OnTrade.             |
//|  Extracted from TradingBotZmqEA.mq5 (no behavior change).        |
//+------------------------------------------------------------------+
#property strict

#include <JSON/JSON.mqh>
#include "../Domain/Contracts.mqh"
#include "../Domain/PlatformApi.mqh"

//--- Deal tracking cap (for OnTrade fill detection)
const int BROKER_SYNC_MAX_TRACKED_DEALS = 10000;

//+------------------------------------------------------------------+
//| BrokerSync — positions are restored/reported from the broker     |
//|  (the source of truth); fills are detected from deal history.    |
//+------------------------------------------------------------------+
class BrokerSync
{
private:
   IPositionApi    *m_position;
   IDealHistoryApi *m_dealHistory;
   IAccountApi     *m_account;
   ITimeApi        *m_time;
   IOrderTracker   *m_orderTracker;
   IZmqNetwork     *m_network;
   ILogger         *m_logger;
   ulong            m_magicNumber;

   //--- Deal tracking (for OnTrade fill detection)
   ulong            m_processedDeals[];
   long             m_lastDealTime;   // high-water mark: newest seen deal time

public:
   BrokerSync(IPositionApi *position, IDealHistoryApi *dealHistory, IAccountApi *account,
              ITimeApi *time, IOrderTracker *orderTracker, IZmqNetwork *network,
              ILogger *logger, ulong magicNumber)
   {
      m_position = position;
      m_dealHistory = dealHistory;
      m_account = account;
      m_time = time;
      m_orderTracker = orderTracker;
      m_network = network;
      m_logger = logger;
      m_magicNumber = magicNumber;
      m_lastDealTime = 0;
   }

   ~BrokerSync() {}

   // ═══════════════════════════════════════════════════════════════
   // Crash Recovery & Position Sync
   // ═══════════════════════════════════════════════════════════════

   void RestoreFromBroker()
   {
      int total = m_position.Total();
      int restored = 0;

      for(int i = 0; i < total; i++)
      {
         ulong ticket = m_position.TicketByIndex(i);
         if(ticket == 0) continue;
         if(m_position.Magic() != (long)m_magicNumber) continue;

         string tradeId = m_position.Comment();
         if(StringLen(tradeId) == 0) tradeId = "mt5_" + IntegerToString(ticket);

         double slPoints = 0;
         double rrRatio = 1.0;
         m_orderTracker.TrackEntry(tradeId, ticket, slPoints, rrRatio);
         restored++;
      }

      if(restored > 0)
         m_logger.Info("[Sync] Restored " + IntegerToString(restored) + " position(s) from broker");
   }

   void ReportPositionsToPython()
   {
      int total = m_position.Total();
      JSONValue *positions = new JSONValue(JSON_ARRAY);
      JSONValue *untracked = new JSONValue(JSON_ARRAY);
      int count = 0;
      int untrackedCount = 0;
      string accountName = TerminalAccountLogin();

      // Build set of tracked trade IDs for orphan detection
      string trackedTradeIds[];
      if(m_orderTracker != NULL)
         m_orderTracker.GetActiveTradeIds(trackedTradeIds);

      for(int i = 0; i < total; i++)
      {
         ulong ticket = m_position.TicketByIndex(i);
         if(ticket == 0) continue;
         if(m_position.Magic() != (long)m_magicNumber) continue;

         string tradeId = m_position.Comment();
         if(StringLen(tradeId) == 0) tradeId = "mt5_" + IntegerToString(ticket);

         JSONValue *pos = new JSONValue(JSON_OBJECT);
         pos["trade_id"]   = new JSONValue(tradeId);
         pos["direction"]  = new JSONValue((m_position.Type() == POSITION_TYPE_BUY) ? "long" : "short");
         pos["entry_price"]= new JSONValue(m_position.PriceOpen());
         pos["stop_loss"]  = new JSONValue(m_position.StopLoss());
         pos["take_profit"]= new JSONValue(m_position.TakeProfit());
         pos["quantity"]   = new JSONValue(m_position.Volume());
         pos["account"]    = new JSONValue(accountName);

         // Check if this trade is tracked (orphan detection)
         bool isTracked = false;
         for(int j = 0; j < ArraySize(trackedTradeIds); j++)
         {
            if(trackedTradeIds[j] == tradeId)
            {
               isTracked = true;
               break;
            }
         }

         if(isTracked)
         {
            positions.Add(pos);
            count++;
         }
         else
         {
            untracked.Add(pos);
            untrackedCount++;
         }
         // NOTE: Add() takes ownership — do NOT delete pos
      }

      m_network.SendPositionSync(positions, untracked, count);
      // NOTE: SendPositionSync puts arrays into a payload tree which is then deleted.
      // Do NOT delete positions or untracked here.

      if(count > 0 || untrackedCount > 0)
      {
         if(m_logger != NULL)
            m_logger.Info("[Sync] Reported " + IntegerToString(count) + " position(s) to Python (broker is source of truth)");
         if(m_logger != NULL && untrackedCount > 0)
            m_logger.Warning("[Sync] Found " + IntegerToString(untrackedCount) + " untracked position(s) on broker");
      }
   }

   // ═══════════════════════════════════════════════════════════════
   // Fill Detection (OnTrade)
   // ═══════════════════════════════════════════════════════════════

   void ProcessNewDeals()
   {
      if(m_network == NULL || m_logger == NULL) return;

      // Load recent history (last hour)
      datetime from = m_time.Now() - 3600;
      if(from < 0) from = 0;
      m_dealHistory.Select(from, m_time.Now());

      string accountName = TerminalAccountLogin();
      double accountBalance = m_account.Balance();

      int total = m_dealHistory.Total();
      long maxDealTime = m_lastDealTime;
      for(int i = total - 1; i >= 0; i--)
      {
         ulong ticket = m_dealHistory.TicketByIndex(i);
         if(ticket == 0) continue;
         long dealTime = m_dealHistory.Time(ticket);

         //--- Watermark: history is chronological and we iterate newest→oldest.
         //--- Deals older than the last pass's newest deal were fully handled
         //--- then — stop. This bound holds even when the processed-ticket
         //--- FIFO overflows (it only dedupes ties at the watermark second).
         if(dealTime < m_lastDealTime) break;
         if(dealTime > maxDealTime) maxDealTime = dealTime;

         //--- Same-second ties and repeat passes: skip already-processed deals.
         //--- (continue, not break — foreign-magic deals are never marked, so
         //--- a processed marker does not imply older deals are done when the
         //--- timestamps are equal.)
         if(IsDealProcessed(ticket)) continue;

         // Check magic number
         ulong magic = (ulong)m_dealHistory.Magic(ticket);
         if(magic != m_magicNumber) continue;

         // Process this deal
         ENUM_DEAL_ENTRY entry = (ENUM_DEAL_ENTRY)m_dealHistory.Entry(ticket);
         ENUM_DEAL_REASON reason = (ENUM_DEAL_REASON)m_dealHistory.Reason(ticket);
         double price = m_dealHistory.Price(ticket);
         string comment = m_dealHistory.Comment(ticket);
         ulong orderTicket = (ulong)m_dealHistory.OrderTicket(ticket);

         if(entry == DEAL_ENTRY_IN)
         {
            // Entry fill
            double sl = 0, tp = 0;
            // Try to get SL/TP from the associated order
            if(m_dealHistory.SelectOrder(orderTicket))
            {
               sl = m_dealHistory.OrderStopLoss(orderTicket);
               tp = m_dealHistory.OrderTakeProfit(orderTicket);
            }
            double volume = m_dealHistory.Volume(ticket);
            m_network.SendEntryFill(comment, price, sl, tp, accountName, volume, accountBalance);
            m_network.SendTradeLog(comment, "MT5:FILL", "Entry filled @ " + DoubleToString(price, 5));
            m_logger.Success("ENTRY FILL: " + comment + " @ " + DoubleToString(price, 5));
         }
         else if(entry == DEAL_ENTRY_OUT || entry == DEAL_ENTRY_OUT_BY)
         {
            // Exit fill
            string resultType = "CLOSE";
            if(reason == DEAL_REASON_SL) resultType = "SL";
            else if(reason == DEAL_REASON_TP) resultType = "TP";

            double realizedPnl = m_dealHistory.Profit(ticket);
            double commission = m_dealHistory.Commission(ticket);

            m_network.SendExitFill(comment, price, resultType, dealTime, accountName, realizedPnl, commission, accountBalance);
            m_network.SendTradeLog(comment, "MT5:FILL", resultType + " filled @ " + DoubleToString(price, 5));
            m_logger.Info("EXIT FILL (" + resultType + "): " + comment + " @ " + DoubleToString(price, 5));

            // Clean up tracking
            if(m_orderTracker != NULL)
               m_orderTracker.RemoveTrade(comment);
         }

         MarkDealProcessed(ticket);
      }

      m_lastDealTime = maxDealTime;
   }

private:
   // Canonical account name echoed in fills and position_sync entries
   // (MetaTrader is single-account; this is the broker login ID).
   string TerminalAccountLogin()
   {
      return IntegerToString(m_account.Login());
   }

   bool IsDealProcessed(ulong ticket)
   {
      int size = ArraySize(m_processedDeals);
      for(int i = 0; i < size; i++)
         if(m_processedDeals[i] == ticket)
            return true;
      return false;
   }

   void MarkDealProcessed(ulong ticket)
   {
      int size = ArraySize(m_processedDeals);
      if(size >= BROKER_SYNC_MAX_TRACKED_DEALS)
      {
         // Shift array left (FIFO)
         for(int i = 1; i < size; i++)
            m_processedDeals[i - 1] = m_processedDeals[i];
         m_processedDeals[size - 1] = ticket;
      }
      else
      {
         ArrayResize(m_processedDeals, size + 1);
         m_processedDeals[size] = ticket;
      }
   }
};
