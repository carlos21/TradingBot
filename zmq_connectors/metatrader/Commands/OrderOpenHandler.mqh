//+------------------------------------------------------------------+
//|                                  Commands/OrderOpenHandler.mqh   |
//|  Handles order_open command: market entry + dynamic lot sizing.  |
//+------------------------------------------------------------------+
#property strict

#include "../Domain/Contracts.mqh"
#include "../Domain/MessageTypes.mqh"
#include "../Domain/ValueObjects.mqh"

//+------------------------------------------------------------------+
//| OrderOpenHandler — executes market entry orders                  |
//+------------------------------------------------------------------+
class OrderOpenHandler : public ICommandHandler
{
private:
   IZmqNetwork  *m_network;
   ILogger      *m_logger;
   IOrderTracker *m_tracker;
   ulong         m_magicNumber;
   string        m_symbol;

public:
   OrderOpenHandler(IZmqNetwork *network, ILogger *logger, IOrderTracker *tracker, ulong magicNumber, string symbol)
   {
      m_network = network;
      m_logger = logger;
      m_tracker = tracker;
      m_magicNumber = magicNumber;
      m_symbol = symbol;
   }

   ~OrderOpenHandler() {}

   //--- ICommandHandler implementation
   bool CanHandle(string msgType) override
   {
      return (msgType == MT_ORDER_OPEN);
   }

   bool Handle(MessageEnvelope *envelope) override
   {
      if(envelope == NULL || envelope.root == NULL)
      {
         if(m_logger != NULL)
            m_logger->Warning("OrderOpenHandler: empty envelope");
         return false;
      }

      // Extract fields from payload
      string tradeId    = envelope.PayloadString("trade_id");
      string direction  = envelope.PayloadString("direction");
      double entryPrice = envelope.PayloadDouble("entry_price");
      double sl         = envelope.PayloadDouble("stop_loss");
      double tp         = envelope.PayloadDouble("take_profit");
      double riskPoints = envelope.PayloadDouble("risk_points");
      double rrRatio    = envelope.PayloadDouble("rr_ratio");
      if(rrRatio <= 0) rrRatio = 1.0;
      double riskUsd    = envelope.PayloadDouble("risk_usd");

      if(StringLen(tradeId) == 0 || StringLen(direction) == 0)
      {
         if(m_logger != NULL)
            m_logger->Error("OrderOpenHandler: missing trade_id or direction");
         return false;
      }

      // Calculate dynamic lot size
      double volume = CalculateLotSize(riskUsd, riskPoints);
      if(volume <= 0)
      {
         if(m_logger != NULL)
            m_logger->Error("OrderOpenHandler: calculated volume is zero");
         return false;
      }

      // Build trade request
      ENUM_ORDER_TYPE orderType = (direction == "long") ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
      double price = (orderType == ORDER_TYPE_BUY)
                        ? SymbolInfoDouble(m_symbol, SYMBOL_ASK)
                        : SymbolInfoDouble(m_symbol, SYMBOL_BID);

      MqlTradeRequest request = {};
      MqlTradeResult result = {};
      request.action    = TRADE_ACTION_DEAL;
      request.symbol    = m_symbol;
      request.volume    = volume;
      request.type      = orderType;
      request.price     = price;
      request.sl        = sl;
      request.tp        = tp;
      request.deviation = 10;
      request.magic     = m_magicNumber;
      request.comment   = tradeId;

      if(m_logger != NULL)
         m_logger->Info("Opening " + direction + " " + tradeId + " vol=" + DoubleToString(volume, 2) + " @ " + DoubleToString(price, 5));

      if(!OrderSend(request, result))
      {
         int err = GetLastError();
         if(m_logger != NULL)
            m_logger->Error("OrderSend failed for " + tradeId + " err=" + IntegerToString(err));
         m_network.SendError("metatrader5", "order_open_failed", "OrderSend err=" + IntegerToString(err));
         return false;
      }

      if(result.retcode == TRADE_RETCODE_DONE || result.retcode == TRADE_RETCODE_PLACED)
      {
         // Track the order
         if(m_tracker != NULL)
            m_tracker->TrackEntry(tradeId, result.order, riskPoints, rrRatio);

         // Send entry fill immediately for market orders (they fill right away)
         // For pending orders we would wait for OnTrade, but this bot uses market orders
         double actualSl = sl;
         double actualTp = tp;
         if(riskPoints > 0)
         {
            // Recalculate SL/TP from actual fill price if needed
            actualSl = (direction == "long") ? (result.price - riskPoints * _Point) : (result.price + riskPoints * _Point);
            actualTp = (direction == "long") ? (result.price + riskPoints * rrRatio * _Point) : (result.price - riskPoints * rrRatio * _Point);
            // Normalize to symbol digits
            int digits = (int)SymbolInfoInteger(m_symbol, SYMBOL_DIGITS);
            actualSl = NormalizeDouble(actualSl, digits);
            actualTp = NormalizeDouble(actualTp, digits);
         }

         m_network.SendEntryFill(tradeId, result.price, actualSl, actualTp);
         m_network.SendTradeLog(tradeId, "MT5:ORDER", "Opened " + direction + " vol=" + DoubleToString(volume, 2));

         if(m_logger != NULL)
            m_logger->Success("Opened " + tradeId + " @ " + DoubleToString(result.price, 5));
         return true;
      }
      else
      {
         if(m_logger != NULL)
            m_logger->Error("Order failed for " + tradeId + " retcode=" + IntegerToString(result.retcode));
         m_network.SendError("metatrader5", "order_open_failed", "Retcode=" + IntegerToString(result.retcode));
         return false;
      }
   }

private:
   //--- Calculate lot size from risk_usd and risk_points
   double CalculateLotSize(double riskUsd, double riskPoints)
   {
      if(riskUsd <= 0 || riskPoints <= 0)
      {
         // Fallback: use minimum volume
         return SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_MIN);
      }

      double tickValue = SymbolInfoDouble(m_symbol, SYMBOL_TRADE_TICK_VALUE);
      double tickSize  = SymbolInfoDouble(m_symbol, SYMBOL_TRADE_TICK_SIZE);
      double point     = SymbolInfoDouble(m_symbol, SYMBOL_POINT);

      if(tickValue <= 0 || tickSize <= 0 || point <= 0)
         return SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_MIN);

      double priceRisk = riskPoints * point;
      double ticksRisk = priceRisk / tickSize;
      if(ticksRisk <= 0)
         return SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_MIN);

      double volume = riskUsd / (ticksRisk * tickValue);

      // Clamp to symbol limits
      double volMin  = SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_MIN);
      double volMax  = SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_MAX);
      double volStep = SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_STEP);

      volume = MathMax(volMin, MathMin(volMax, volume));
      if(volStep > 0)
         volume = MathFloor(volume / volStep) * volStep;

      int digits = (int)SymbolInfoInteger(m_symbol, SYMBOL_VOLUME_DIGITS);
      return NormalizeDouble(volume, digits);
   }
};
