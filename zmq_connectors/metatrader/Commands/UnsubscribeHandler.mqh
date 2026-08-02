//+------------------------------------------------------------------+
//|                                Commands/UnsubscribeHandler.mqh   |
//|  Handles unsubscribe command: stop streaming one instrument.     |
//+------------------------------------------------------------------+
#property strict

#include "../Domain/Contracts.mqh"
#include "../Domain/MessageTypes.mqh"
#include "../Domain/ValueObjects.mqh"
#include "../Application/SubscriptionManager.mqh"

//+------------------------------------------------------------------+
//| UnsubscribeHandler — removes an instrument from the streaming set|
//+------------------------------------------------------------------+
class UnsubscribeHandler : public ICommandHandler
{
private:
   IZmqNetwork         *m_network;
   ILogger             *m_logger;
   SubscriptionManager *m_subscriptions;

public:
   UnsubscribeHandler(IZmqNetwork *network, ILogger *logger, SubscriptionManager *subscriptions)
   {
      m_network = network;
      m_logger = logger;
      m_subscriptions = subscriptions;
   }

   ~UnsubscribeHandler() {}

   //--- ICommandHandler implementation
   bool CanHandle(string msgType) override
   {
      return (msgType == MT_UNSUBSCRIBE);
   }

   bool Handle(MessageEnvelope *envelope) override
   {
      if(envelope == NULL || envelope.root == NULL)
      {
         if(m_logger != NULL)
            m_logger.Warning("UnsubscribeHandler: empty envelope");
         return false;
      }

      string instrument = envelope.PayloadString("instrument");
      if(m_logger != NULL)
         m_logger.Info("UNSUBSCRIBE command: " + (StringLen(instrument) > 0 ? instrument : "<missing>"));

      if(StringLen(instrument) == 0)
      {
         m_network.SendError("metatrader5", "unsubscribe_failed", "instrument is required in unsubscribe payload");
         return false;
      }

      // Idempotent — removing an unknown symbol still succeeds
      if(m_subscriptions != NULL)
         m_subscriptions.Remove(instrument);

      return true;
   }
};
