//+------------------------------------------------------------------+
//|                                 Commands/OrderCloseHandler.mqh   |
//|  Handles order_close command: close open position.               |
//+------------------------------------------------------------------+
#property strict

#include "../Domain/Contracts.mqh"
#include "../Domain/MessageTypes.mqh"
#include "../Domain/ValueObjects.mqh"

//+------------------------------------------------------------------+
//| OrderCloseHandler — closes position by trade_id                  |
//+------------------------------------------------------------------+
class OrderCloseHandler : public ICommandHandler
{
private:
   IZmqNetwork   *m_network;
   ILogger       *m_logger;
   IOrderTracker *m_tracker;
   ulong          m_magicNumber;
   string         m_symbol;

public:
   OrderCloseHandler(IZmqNetwork *network, ILogger *logger, IOrderTracker *tracker, ulong magicNumber, string symbol)
   {
      m_network = network;
      m_logger = logger;
      m_tracker = tracker;
      m_magicNumber = magicNumber;
      m_symbol = symbol;
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

      string tradeId = envelope.PayloadString("trade_id");
      if(StringLen(tradeId) == 0)
      {
         if(m_logger != NULL)
            m_logger.Error("OrderCloseHandler: missing trade_id");
         return false;
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
            m_logger.Error("OrderCloseHandler: no open position for trade_id=" + tradeId);
         m_network.SendError("metatrader5", "order_close_failed", "Position not found: " + tradeId);
         return false;
      }

      if(!PositionSelectByTicket(ticket))
      {
         if(m_logger != NULL)
            m_logger.Error("OrderCloseHandler: PositionSelectByTicket failed for " + tradeId);
         return false;
      }

      // Build close request
      ENUM_POSITION_TYPE posType = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      ENUM_ORDER_TYPE closeType = (posType == POSITION_TYPE_BUY) ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
      double closePrice = (closeType == ORDER_TYPE_SELL)
                           ? SymbolInfoDouble(m_symbol, SYMBOL_BID)
                           : SymbolInfoDouble(m_symbol, SYMBOL_ASK);
      double volume = PositionGetDouble(POSITION_VOLUME);

      MqlTradeRequest request = {};
      MqlTradeResult result = {};
      request.action   = TRADE_ACTION_DEAL;
      request.position = ticket;
      request.symbol   = m_symbol;
      request.volume   = volume;
      request.type     = closeType;
      request.price    = closePrice;
      request.deviation = 10;
      request.magic    = m_magicNumber;

      if(m_logger != NULL)
         m_logger.Info("Closing " + tradeId + " vol=" + DoubleToString(volume, 2) + " @ " + DoubleToString(closePrice, 5));

      if(!OrderSend(request, result))
      {
         int err = GetLastError();
         if(m_logger != NULL)
            m_logger.Error("Close OrderSend failed for " + tradeId + " err=" + IntegerToString(err));
         return false;
      }

      if(result.retcode == TRADE_RETCODE_DONE)
      {
         m_network.SendExitFill(tradeId, result.price, "CLOSE");
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
         return false;
      }
   }

private:
   //--- Find position ticket by comment (trade_id)
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
