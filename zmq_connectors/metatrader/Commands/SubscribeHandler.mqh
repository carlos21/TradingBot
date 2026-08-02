//+------------------------------------------------------------------+
//|                                 Commands/SubscribeHandler.mqh    |
//|  Handles subscribe command: start streaming one instrument.      |
//|  Mirrors NinjaTrader SubscribeHandler semantics.                 |
//+------------------------------------------------------------------+
#property strict

#include "../Domain/Contracts.mqh"
#include "../Domain/MessageTypes.mqh"
#include "../Domain/ValueObjects.mqh"
#include "../Application/SubscriptionManager.mqh"

//+------------------------------------------------------------------+
//| SubscribeHandler — adds an instrument to the streaming set       |
//+------------------------------------------------------------------+
class SubscribeHandler : public ICommandHandler
{
private:
   IZmqNetwork         *m_network;
   ILogger             *m_logger;
   SubscriptionManager *m_subscriptions;

public:
   SubscribeHandler(IZmqNetwork *network, ILogger *logger, SubscriptionManager *subscriptions)
   {
      m_network = network;
      m_logger = logger;
      m_subscriptions = subscriptions;
   }

   ~SubscribeHandler() {}

   //--- ICommandHandler implementation
   bool CanHandle(string msgType) override
   {
      return (msgType == MT_SUBSCRIBE);
   }

   bool Handle(MessageEnvelope *envelope) override
   {
      if(envelope == NULL || envelope.root == NULL)
      {
         if(m_logger != NULL)
            m_logger.Warning("SubscribeHandler: empty envelope");
         return false;
      }

      string instrument = envelope.PayloadString("instrument");
      if(m_logger != NULL)
         m_logger.Info("SUBSCRIBE command: " + (StringLen(instrument) > 0 ? instrument : "<missing>"));

      if(StringLen(instrument) == 0)
      {
         m_network.SendError("metatrader5", "subscribe_failed", "instrument is required in subscribe payload");
         return false;
      }

      if(m_subscriptions == NULL || !m_subscriptions.Add(instrument))
      {
         m_network.SendError("metatrader5", "subscribe_failed", "unknown symbol: " + instrument);
         return false;
      }

      return true;
   }
};
