//+------------------------------------------------------------------+
//|                                Commands/OrderModifyHandler.mqh   |
//|  Handles order_modify command: move SL/TP via TRADE_ACTION_SLTP  |
//|  Unlike NinjaTrader, MT5 supports atomic SL/TP modification.     |
//+------------------------------------------------------------------+
#property strict

#include "../Domain/Contracts.mqh"
#include "../Domain/MessageTypes.mqh"
#include "../Domain/ValueObjects.mqh"

//+------------------------------------------------------------------+
//| OrderModifyHandler — modifies SL/TP of an open position          |
//+------------------------------------------------------------------+
class OrderModifyHandler : public ICommandHandler
{
private:
   IZmqNetwork   *m_network;
   ILogger       *m_logger;
   IOrderTracker *m_tracker;
   ulong          m_magicNumber;
   string         m_symbol;

public:
   OrderModifyHandler(IZmqNetwork *network, ILogger *logger, IOrderTracker *tracker, ulong magicNumber, string symbol)
   {
      m_network = network;
      m_logger = logger;
      m_tracker = tracker;
      m_magicNumber = magicNumber;
      m_symbol = symbol;
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

      string tradeId = envelope.PayloadString("trade_id");
      if(StringLen(tradeId) == 0)
      {
         if(m_logger != NULL)
            m_logger.Error("OrderModifyHandler: missing trade_id");
         return false;
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

      if(!PositionSelectByTicket(ticket))
      {
         if(m_logger != NULL)
            m_logger.Error("OrderModifyHandler: PositionSelectByTicket failed");
         return false;
      }

      // Get new SL/TP from payload, or keep current if not provided
      double newSl = envelope.PayloadDouble("stop_loss");
      double newTp = envelope.PayloadDouble("take_profit");

      if(newSl == 0)
         newSl = PositionGetDouble(POSITION_SL);
      if(newTp == 0)
         newTp = PositionGetDouble(POSITION_TP);

      // Build modify request
      MqlTradeRequest request = {};
      MqlTradeResult result = {};
      request.action   = TRADE_ACTION_SLTP;
      request.position = ticket;
      request.symbol   = m_symbol;
      request.sl       = newSl;
      request.tp       = newTp;

      if(m_logger != NULL)
         m_logger.Info("Modifying " + tradeId + " SL=" + DoubleToString(newSl, 5) + " TP=" + DoubleToString(newTp, 5));

      if(!OrderSend(request, result))
      {
         int err = GetLastError();
         if(m_logger != NULL)
            m_logger.Error("Modify OrderSend failed for " + tradeId + " err=" + IntegerToString(err));
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
         return false;
      }
   }

private:
   ulong FindTicketByComment(string tradeId)
   {
      int total = PositionsTotal();
      for(int i = 0; i < total; i++)
      {
         ulong ticket = PositionGetTicket(i);
         if(ticket == 0) continue;
         if(PositionGetInteger(POSITION_MAGIC) != (long)m_magicNumber) continue;
         if(PositionGetString(POSITION_COMMENT) == tradeId)
            return ticket;
      }
      return 0;
   }
};
