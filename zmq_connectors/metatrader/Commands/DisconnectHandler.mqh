//+------------------------------------------------------------------+
//|                                 Commands/DisconnectHandler.mqh   |
//|  Handles disconnect command: deliberate Python stream stop.      |
//|  ACK is sent by the EA command loop right after Handle() returns;|
//|  the actual teardown is deferred ~100ms via g_disconnectRequest  |
//|  so the ACK reaches Python first and the deliberate stop does    |
//|  NOT trigger connection recovery (mirrors NT DisconnectHandler). |
//+------------------------------------------------------------------+
#property strict

#include "../Domain/Contracts.mqh"
#include "../Domain/MessageTypes.mqh"
#include "../Domain/ValueObjects.mqh"

//+------------------------------------------------------------------+
//| DisconnectHandler — quiet, deliberate shutdown of the stream     |
//+------------------------------------------------------------------+
class DisconnectHandler : public ICommandHandler
{
private:
   IZmqNetwork *m_network;
   ILogger     *m_logger;

public:
   DisconnectHandler(IZmqNetwork *network, ILogger *logger)
   {
      m_network = network;
      m_logger = logger;
   }

   ~DisconnectHandler() {}

   //--- ICommandHandler implementation
   bool CanHandle(string msgType) override
   {
      return (msgType == MT_DISCONNECT);
   }

   bool Handle(MessageEnvelope *envelope) override
   {
      if(envelope == NULL || envelope.root == NULL)
      {
         if(m_logger != NULL)
            m_logger.Warning("DisconnectHandler: empty envelope");
         return false;
      }

      string reason = envelope.PayloadString("reason", "stream stopped");
      if(m_logger != NULL)
         m_logger.Info("DISCONNECT command: " + reason);

      // Defer socket teardown — the EA picks this up in OnTick/OnTimer
      // after the success ACK has had time to go out.
      g_disconnectRequestTick = (long)GetTickCount();

      return true;
   }
};
