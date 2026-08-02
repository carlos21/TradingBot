//+------------------------------------------------------------------+
//|                                 Commands/OrderCloseHandler.mqh   |
//|  Handles order_close command: close open position.               |
//|  Validates the payload account (single-account terminal) and     |
//|  resolves the broker symbol from the required 'instrument' field.|
//+------------------------------------------------------------------+
#property strict

#include "../Domain/Contracts.mqh"
#include "../Domain/MessageTypes.mqh"
#include "../Domain/ValueObjects.mqh"
#include "../Domain/PlatformApi.mqh"
#include "../Domain/AccountValidator.mqh"

//+------------------------------------------------------------------+
//| OrderCloseHandler — closes position by trade_id                  |
//+------------------------------------------------------------------+
class OrderCloseHandler : public ICommandHandler
{
private:
   IZmqNetwork      *m_network;
   ILogger          *m_logger;
   IOrderTracker    *m_tracker;
   ZmqConfiguration *m_config;
   PlatformApis     *m_apis;
   bool              m_simulate;

public:
   OrderCloseHandler(IZmqNetwork *network, ILogger *logger, IOrderTracker *tracker, ZmqConfiguration *config, PlatformApis *apis, bool simulate = false)
   {
      m_network = network;
      m_logger = logger;
      m_tracker = tracker;
      m_config = config;
      m_apis = apis;
      m_simulate = simulate;
   }

   ~OrderCloseHandler() {}

   //--- ICommandHandler implementation
   bool CanHandle(string msgType) override
   {
      return (msgType == MT_ORDER_CLOSE);
   }

   bool Handle(MessageEnvelope *envelope) override
   {
      if(envelope == NULL || envelope.root == NULL)
      {
         if(m_logger != NULL)
            m_logger.Warning("OrderCloseHandler: empty envelope");
         return false;
      }

      string tradeId    = envelope.PayloadString("trade_id");
      string instrument = envelope.PayloadString("instrument");
      string account    = envelope.PayloadString("account");

      if(StringLen(tradeId) == 0)
      {
         if(m_logger != NULL)
            m_logger.Error("OrderCloseHandler: missing trade_id");
         return false;
      }

      // instrument is REQUIRED — it carries the MT5 broker symbol
      if(StringLen(instrument) == 0)
      {
         if(m_logger != NULL)
            m_logger.Error("OrderCloseHandler: instrument is required");
         m_network.SendError("metatrader5", "order_close_failed", "instrument is required in order_close payload");
         return false;
      }

      // Account validation (single-account terminal — never silently fall back)
      string resolvedAccount;
      if(!AccountValidator::Validate(m_apis.account, account, resolvedAccount))
      {
         string msg = "account '" + account + "' does not match terminal account '" + resolvedAccount + "'";
         if(m_logger != NULL)
            m_logger.Error("OrderCloseHandler: " + msg);
         m_network.SendError("metatrader5", "account_mismatch", msg);
         return false;
      }

      // Resolve the symbol at the broker — fail if it does not exist
      if(!m_apis.symbol.Select(instrument))
      {
         if(m_logger != NULL)
            m_logger.Error("OrderCloseHandler: unknown symbol '" + instrument + "'");
         m_network.SendError("metatrader5", "order_close_failed", "unknown symbol: " + instrument);
         return false;
      }

      // --- SIMULATE MODE: Send fake exit fill instantly, NO broker interaction ---
      if(m_simulate || g_e2eTestRunning)
      {
         if(m_logger != NULL)
            m_logger.Info("🧪 SIMULATE CLOSE: " + tradeId);
         m_network.SendExitFill(tradeId, m_apis.symbol.Bid(instrument), "CLOSE", 0, resolvedAccount);
         m_network.SendTradeLog(tradeId, "MT5:SIMULATE", "Simulated exit fill (close)");
         return true;
      }

      // Guard: if trade is not tracked, it may already be closed
      if(m_tracker != NULL)
      {
         ulong entryTicket = 0;
         double slPoints, rrRatio;
         bool hasEntry = m_tracker.TryGetEntry(tradeId, entryTicket, slPoints, rrRatio);
         ulong stopTicket = 0, targetTicket = 0, closeTicket = 0;
         bool hasStop = m_tracker.TryGetStopLoss(tradeId, stopTicket);
         bool hasTarget = m_tracker.TryGetTakeProfit(tradeId, targetTicket);
         bool hasClose = m_tracker.TryGetCloseOrder(tradeId, closeTicket);
         if(!hasEntry && !hasStop && !hasTarget && !hasClose)
         {
            if(m_logger != NULL)
               m_logger.Warning("[Close:" + tradeId + "] Trade not tracked — already closed or never opened. Ignoring.");
            m_network.SendTradeLog(tradeId, "MT5:WARNING", "Close ignored: trade not tracked");
            return true;
         }
      }

      // Find ticket from tracker
      ulong ticket = 0;
      if(m_tracker != NULL)
      {
         ulong entryTicket = 0;
         double slPoints, rrRatio;
         if(m_tracker.TryGetEntry(tradeId, entryTicket, slPoints, rrRatio))
            ticket = entryTicket;
      }

      // Fallback: search positions by comment (trade_id)
      if(ticket == 0)
         ticket = FindTicketByComment(tradeId);

      if(ticket == 0)
      {
         if(m_logger != NULL)
            m_logger.Warning("OrderCloseHandler: no open position for trade_id=" + tradeId + " — may already be closed");
         // Remove from tracker since position is gone
         if(m_tracker != NULL)
            m_tracker.RemoveTrade(tradeId);
         return true;
      }

      if(!m_apis.position.SelectByTicket(ticket))
      {
         if(m_logger != NULL)
            m_logger.Warning("OrderCloseHandler: PositionSelectByTicket failed for " + tradeId + " — may already be closed");
         if(m_tracker != NULL)
            m_tracker.RemoveTrade(tradeId);
         return true;
      }

      // Build close request
      ENUM_POSITION_TYPE posType = (ENUM_POSITION_TYPE)m_apis.position.Type();
      ENUM_ORDER_TYPE closeType = (posType == POSITION_TYPE_BUY) ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
      double closePrice = (closeType == ORDER_TYPE_SELL)
                           ? m_apis.symbol.Bid(instrument)
                           : m_apis.symbol.Ask(instrument);
      double volume = m_apis.position.Volume();

      MqlTradeRequest request = {};
      TradeResult result;
      request.action   = TRADE_ACTION_DEAL;
      request.position = ticket;
      request.symbol   = instrument;
      request.volume   = volume;
      request.type     = closeType;
      request.price    = closePrice;
      request.deviation = 10;
      request.magic    = m_config.magicNumber;

      if(m_logger != NULL)
         m_logger.Info("Closing " + tradeId + " " + instrument + " vol=" + DoubleToString(volume, 2) + " @ " + DoubleToString(closePrice, 5) + " account=" + resolvedAccount);

      if(!m_apis.trade.Send(request, result))
      {
         int err = result.error;
         if(m_logger != NULL)
            m_logger.Error("Close OrderSend failed for " + tradeId + " err=" + IntegerToString(err));
         m_network.SendOrderRejected(tradeId, "Close OrderSend err=" + IntegerToString(err));
         m_network.SendError("metatrader5", "order_close_failed", "OrderSend err=" + IntegerToString(err));
         return false;
      }

      if(result.retcode == TRADE_RETCODE_DONE)
      {
         m_network.SendExitFill(tradeId, result.price, "CLOSE", 0, resolvedAccount, 0, 0, m_apis.account.Balance());
         m_network.SendTradeLog(tradeId, "MT5:CLOSE", "Position closed @ " + DoubleToString(result.price, 5));

         if(m_tracker != NULL)
            m_tracker.RemoveTrade(tradeId);

         if(m_logger != NULL)
            m_logger.Success("Closed " + tradeId + " @ " + DoubleToString(result.price, 5));
         return true;
      }
      else
      {
         if(m_logger != NULL)
            m_logger.Error("Close failed for " + tradeId + " retcode=" + IntegerToString(result.retcode));
         m_network.SendOrderRejected(tradeId, "Close retcode=" + IntegerToString(result.retcode));
         m_network.SendError("metatrader5", "order_close_failed", "Retcode=" + IntegerToString(result.retcode));
         return false;
      }
   }

private:
   //--- Find position ticket by comment (trade_id)
   ulong FindTicketByComment(string tradeId)
   {
      int total = m_apis.position.Total();
      for(int i = 0; i < total; i++)
      {
         ulong ticket = m_apis.position.TicketByIndex(i);
         if(ticket == 0) continue;
         if(m_apis.position.Magic() != (long)m_config.magicNumber) continue;
         if(m_apis.position.Comment() == tradeId)
            return ticket;
      }
      return 0;
   }
};
