//+------------------------------------------------------------------+
//|                                Commands/OrderModifyHandler.mqh   |
//|  Handles order_modify command: move SL/TP via TRADE_ACTION_SLTP  |
//|  Unlike NinjaTrader, MT5 supports atomic SL/TP modification.     |
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
//| OrderModifyHandler — modifies SL/TP of an open position          |
//+------------------------------------------------------------------+
class OrderModifyHandler : public ICommandHandler
{
private:
   IZmqNetwork      *m_network;
   ILogger          *m_logger;
   IOrderTracker    *m_tracker;
   ZmqConfiguration *m_config;
   PlatformApis     *m_apis;
   bool              m_simulate;

public:
   OrderModifyHandler(IZmqNetwork *network, ILogger *logger, IOrderTracker *tracker, ZmqConfiguration *config, PlatformApis *apis, bool simulate = false)
   {
      m_network = network;
      m_logger = logger;
      m_tracker = tracker;
      m_config = config;
      m_apis = apis;
      m_simulate = simulate;
   }

   ~OrderModifyHandler() {}

   //--- ICommandHandler implementation
   bool CanHandle(string msgType) override
   {
      return (msgType == MT_ORDER_MODIFY);
   }

   bool Handle(MessageEnvelope *envelope) override
   {
      if(envelope == NULL || envelope.root == NULL)
      {
         if(m_logger != NULL)
            m_logger.Warning("OrderModifyHandler: empty envelope");
         return false;
      }

      string tradeId    = envelope.PayloadString("trade_id");
      string instrument = envelope.PayloadString("instrument");
      string account    = envelope.PayloadString("account");

      if(StringLen(tradeId) == 0)
      {
         if(m_logger != NULL)
            m_logger.Error("OrderModifyHandler: missing trade_id");
         return false;
      }

      // instrument is REQUIRED — it carries the MT5 broker symbol
      if(StringLen(instrument) == 0)
      {
         if(m_logger != NULL)
            m_logger.Error("OrderModifyHandler: instrument is required");
         m_network.SendError("metatrader5", "order_modify_failed", "instrument is required in order_modify payload");
         return false;
      }

      // Account validation (single-account terminal — never silently fall back)
      string resolvedAccount;
      if(!AccountValidator::Validate(m_apis.account, account, resolvedAccount))
      {
         string msg = "account '" + account + "' does not match terminal account '" + resolvedAccount + "'";
         if(m_logger != NULL)
            m_logger.Error("OrderModifyHandler: " + msg);
         m_network.SendError("metatrader5", "account_mismatch", msg);
         return false;
      }

      // Resolve the symbol at the broker — fail if it does not exist
      if(!m_apis.symbol.Select(instrument))
      {
         if(m_logger != NULL)
            m_logger.Error("OrderModifyHandler: unknown symbol '" + instrument + "'");
         m_network.SendError("metatrader5", "order_modify_failed", "unknown symbol: " + instrument);
         return false;
      }

      // --- SIMULATE MODE: Log and return success, NO broker interaction ---
      if(m_simulate || g_e2eTestRunning)
      {
         double newSl = envelope.PayloadDouble("stop_loss");
         double newTp = envelope.PayloadDouble("take_profit");
         if(m_logger != NULL)
            m_logger.Info("🧪 SIMULATE MODIFY: " + tradeId + " SL=" + DoubleToString(newSl, 5) + " TP=" + DoubleToString(newTp, 5));
         m_network.SendTradeLog(tradeId, "MT5:SIMULATE", "Simulated modify SL=" + DoubleToString(newSl, 5) + " TP=" + DoubleToString(newTp, 5));
         return true;
      }

      // Find ticket
      ulong ticket = 0;
      if(m_tracker != NULL)
      {
         ulong entryTicket = 0;
         double slPoints, rrRatio;
         if(m_tracker.TryGetEntry(tradeId, entryTicket, slPoints, rrRatio))
            ticket = entryTicket;
      }

      if(ticket == 0)
         ticket = FindTicketByComment(tradeId);

      if(ticket == 0)
      {
         if(m_logger != NULL)
            m_logger.Error("OrderModifyHandler: no position for trade_id=" + tradeId);
         return false;
      }

      if(!m_apis.position.SelectByTicket(ticket))
      {
         if(m_logger != NULL)
            m_logger.Error("OrderModifyHandler: PositionSelectByTicket failed");
         return false;
      }

      // Get new SL/TP from payload, or keep current if not provided
      double newSl = envelope.PayloadDouble("stop_loss");
      double newTp = envelope.PayloadDouble("take_profit");

      if(newSl == 0)
         newSl = m_apis.position.StopLoss();
      if(newTp == 0)
         newTp = m_apis.position.TakeProfit();

      // Build modify request
      MqlTradeRequest request = {};
      TradeResult result;
      request.action   = TRADE_ACTION_SLTP;
      request.position = ticket;
      request.symbol   = instrument;
      request.sl       = newSl;
      request.tp       = newTp;

      if(m_logger != NULL)
         m_logger.Info("Modifying " + tradeId + " " + instrument + " SL=" + DoubleToString(newSl, 5) + " TP=" + DoubleToString(newTp, 5) + " account=" + resolvedAccount);

      if(!m_apis.trade.Send(request, result))
      {
         int err = result.error;
         if(m_logger != NULL)
            m_logger.Error("Modify OrderSend failed for " + tradeId + " err=" + IntegerToString(err));
         m_network.SendError("metatrader5", "order_modify_failed", "OrderSend err=" + IntegerToString(err));
         return false;
      }

      if(result.retcode == TRADE_RETCODE_DONE)
      {
         m_network.SendTradeLog(tradeId, "MT5:MODIFY", "SL/TP changed to SL=" + DoubleToString(newSl, 5) + " TP=" + DoubleToString(newTp, 5));
         if(m_logger != NULL)
            m_logger.Success("Modified " + tradeId + " SL=" + DoubleToString(newSl, 5) + " TP=" + DoubleToString(newTp, 5));
         return true;
      }
      else
      {
         if(m_logger != NULL)
            m_logger.Error("Modify failed for " + tradeId + " retcode=" + IntegerToString(result.retcode));
         m_network.SendError("metatrader5", "order_modify_failed", "Retcode=" + IntegerToString(result.retcode));
         return false;
      }
   }

private:
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
