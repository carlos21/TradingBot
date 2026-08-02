//+------------------------------------------------------------------+
//|                                  Commands/OrderOpenHandler.mqh   |
//|  Handles order_open command: market entry + dynamic lot sizing.  |
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
//| OrderOpenHandler — executes market entry orders                  |
//+------------------------------------------------------------------+
class OrderOpenHandler : public ICommandHandler
{
private:
   IZmqNetwork      *m_network;
   ILogger          *m_logger;
   IOrderTracker    *m_tracker;
   ZmqConfiguration *m_config;
   PlatformApis     *m_apis;
   bool              m_simulate;

public:
   OrderOpenHandler(IZmqNetwork *network, ILogger *logger, IOrderTracker *tracker, ZmqConfiguration *config, PlatformApis *apis, bool simulate = false)
   {
      m_network = network;
      m_logger = logger;
      m_tracker = tracker;
      m_config = config;
      m_apis = apis;
      m_simulate = simulate;
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
            m_logger.Warning("OrderOpenHandler: empty envelope");
         return false;
      }

      // Extract fields from payload
      string tradeId    = envelope.PayloadString("trade_id");
      string direction  = envelope.PayloadString("direction");
      string instrument = envelope.PayloadString("instrument");
      string account    = envelope.PayloadString("account");
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
            m_logger.Error("OrderOpenHandler: missing trade_id or direction");
         return false;
      }

      // instrument is REQUIRED — it carries the MT5 broker symbol
      if(StringLen(instrument) == 0)
      {
         if(m_logger != NULL)
            m_logger.Error("OrderOpenHandler: instrument is required");
         m_network.SendError("metatrader5", "order_open_failed", "instrument is required in order_open payload");
         return false;
      }

      // Account validation (single-account terminal — never silently fall back)
      string resolvedAccount;
      if(!AccountValidator::Validate(m_apis.account, account, resolvedAccount))
      {
         string msg = "account '" + account + "' does not match terminal account '" + resolvedAccount + "'";
         if(m_logger != NULL)
            m_logger.Error("OrderOpenHandler: " + msg);
         m_network.SendError("metatrader5", "account_mismatch", msg);
         return false;
      }

      // Resolve the symbol at the broker — fail if it does not exist
      if(!m_apis.symbol.Select(instrument))
      {
         if(m_logger != NULL)
            m_logger.Error("OrderOpenHandler: unknown symbol '" + instrument + "'");
         m_network.SendError("metatrader5", "order_open_failed", "unknown symbol: " + instrument);
         return false;
      }

      double point  = m_apis.symbol.Point(instrument);
      int    digits = m_apis.symbol.Digits(instrument);

      // --- SIMULATE MODE: Send fake fill instantly, NO broker interaction ---
      if(m_simulate || g_e2eTestRunning)
      {
         double simPrice = (entryPrice > 0) ? entryPrice : m_apis.symbol.Bid(instrument);
         double simSl = sl;
         double simTp = tp;
         if(simSl == 0 && riskPoints > 0)
            simSl = (direction == "long") ? (simPrice - riskPoints * point) : (simPrice + riskPoints * point);
         if(simTp == 0 && riskPoints > 0 && rrRatio > 0)
            simTp = (direction == "long") ? (simPrice + riskPoints * rrRatio * point) : (simPrice - riskPoints * rrRatio * point);
         simSl = NormalizeDouble(simSl, digits);
         simTp = NormalizeDouble(simTp, digits);

         if(m_logger != NULL)
            m_logger.Info("🧪 SIMULATE OPEN: " + tradeId + " " + direction + " " + instrument + " @ " + DoubleToString(simPrice, 5) + " SL=" + DoubleToString(simSl, 5) + " TP=" + DoubleToString(simTp, 5));
         m_network.SendEntryFill(tradeId, simPrice, simSl, simTp, resolvedAccount);
         m_network.SendTradeLog(tradeId, "MT5:SIMULATE", "Simulated entry fill " + direction + " @ " + DoubleToString(simPrice, 5));
         return true;
      }

      // Guard: prevent duplicate open for same trade_id
      if(m_tracker != NULL)
      {
         ulong existingTicket = 0;
         double existingSl = 0, existingRr = 0;
         if(m_tracker.TryGetEntry(tradeId, existingTicket, existingSl, existingRr))
         {
            if(m_logger != NULL)
               m_logger.Warning("Duplicate place_order for " + tradeId + ", ignoring");
            m_network.SendTradeLog(tradeId, "MT5:WARNING", "Duplicate place_order request ignored");
            return true;
         }
      }

      // Calculate dynamic lot size
      double volume = CalculateLotSize(instrument, riskUsd, riskPoints);
      if(volume <= 0)
      {
         if(m_logger != NULL)
            m_logger.Error("OrderOpenHandler: calculated volume is zero");
         return false;
      }

      // Build trade request
      ENUM_ORDER_TYPE orderType = (direction == "long") ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
      double price = (orderType == ORDER_TYPE_BUY)
                        ? m_apis.symbol.Ask(instrument)
                        : m_apis.symbol.Bid(instrument);

      MqlTradeRequest request = {};
      TradeResult result;
      request.action    = TRADE_ACTION_DEAL;
      request.symbol    = instrument;
      request.volume    = volume;
      request.type      = orderType;
      request.price     = price;
      request.sl        = sl;
      request.tp        = tp;
      request.deviation = 10;
      request.magic     = m_config.magicNumber;
      request.comment   = tradeId;

      if(m_logger != NULL)
         m_logger.Info("Opening " + direction + " " + tradeId + " " + instrument + " vol=" + DoubleToString(volume, 2) + " @ " + DoubleToString(price, 5) + " account=" + resolvedAccount);

      if(!m_apis.trade.Send(request, result))
      {
         int err = result.error;
         if(m_logger != NULL)
            m_logger.Error("OrderSend failed for " + tradeId + " err=" + IntegerToString(err));
         m_network.SendOrderRejected(tradeId, "OrderSend err=" + IntegerToString(err));
         m_network.SendError("metatrader5", "order_open_failed", "OrderSend err=" + IntegerToString(err));
         return false;
      }

      if(result.retcode == TRADE_RETCODE_DONE || result.retcode == TRADE_RETCODE_PLACED)
      {
         // Track the order
         if(m_tracker != NULL)
            m_tracker.TrackEntry(tradeId, result.order, riskPoints, rrRatio);

         // Send entry fill immediately for market orders (they fill right away)
         // For pending orders we would wait for OnTrade, but this bot uses market orders
         double actualSl = sl;
         double actualTp = tp;
         if(riskPoints > 0)
         {
            // Recalculate SL/TP from actual fill price if needed
            actualSl = (direction == "long") ? (result.price - riskPoints * point) : (result.price + riskPoints * point);
            actualTp = (direction == "long") ? (result.price + riskPoints * rrRatio * point) : (result.price - riskPoints * rrRatio * point);
            actualSl = NormalizeDouble(actualSl, digits);
            actualTp = NormalizeDouble(actualTp, digits);
         }

         double filledVolume = (result.volume > 0) ? result.volume : volume;
         m_network.SendEntryFill(tradeId, result.price, actualSl, actualTp, resolvedAccount, filledVolume, m_apis.account.Balance());
         m_network.SendTradeLog(tradeId, "MT5:ORDER", "Opened " + direction + " vol=" + DoubleToString(volume, 2));

         if(m_logger != NULL)
            m_logger.Success("Opened " + tradeId + " @ " + DoubleToString(result.price, 5));
         return true;
      }
      else
      {
         if(m_logger != NULL)
            m_logger.Error("Order failed for " + tradeId + " retcode=" + IntegerToString(result.retcode));
         m_network.SendOrderRejected(tradeId, "Retcode=" + IntegerToString(result.retcode));
         m_network.SendError("metatrader5", "order_open_failed", "Retcode=" + IntegerToString(result.retcode));
         return false;
      }
   }

private:
   //--- Calculate lot size from risk_usd and risk_points
   double CalculateLotSize(string symbol, double riskUsd, double riskPoints)
   {
      if(riskUsd <= 0 || riskPoints <= 0)
      {
         // Fallback: use minimum volume
         return m_apis.symbol.VolumeMin(symbol);
      }

      double tickValue = m_apis.symbol.TickValue(symbol);
      double tickSize  = m_apis.symbol.TickSize(symbol);
      double point     = m_apis.symbol.Point(symbol);

      if(tickValue <= 0 || tickSize <= 0 || point <= 0)
         return m_apis.symbol.VolumeMin(symbol);

      double priceRisk = riskPoints * point;
      double ticksRisk = priceRisk / tickSize;
      if(ticksRisk <= 0)
         return m_apis.symbol.VolumeMin(symbol);

      double volume = riskUsd / (ticksRisk * tickValue);

      // Clamp to symbol limits
      double volMin  = m_apis.symbol.VolumeMin(symbol);
      double volMax  = m_apis.symbol.VolumeMax(symbol);
      double volStep = m_apis.symbol.VolumeStep(symbol);

      volume = MathMax(volMin, MathMin(volMax, volume));
      if(volStep > 0)
         volume = MathFloor(volume / volStep) * volStep;

      int volDigits = 0;
      if(volStep > 0)
         volDigits = (int)(-MathLog10(volStep) + 0.5);
      return NormalizeDouble(volume, volDigits);
   }
};
