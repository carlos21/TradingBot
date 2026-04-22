//+------------------------------------------------------------------+
//|                              Application/CommandDispatcher.mqh   |
//|  Chain-of-Responsibility router for incoming commands.           |
//|  Each handler decides if it can handle the msg_type.             |
//+------------------------------------------------------------------+
#property strict

#include "../Domain/Contracts.mqh"
#include "../Domain/ValueObjects.mqh"

//+------------------------------------------------------------------+
//| CommandDispatcher — routes commands to the first handler         |
//|  that claims it CanHandle(msgType).                              |
//+------------------------------------------------------------------+
class CommandDispatcher : public ICommandDispatcher
{
private:
   ICommandHandler *m_handlers[];
   ILogger         *m_logger;
   int              m_count;

public:
   CommandDispatcher(ILogger *logger)
   {
      m_logger = logger;
      m_count = 0;
   }

   ~CommandDispatcher()
   {
      // Note: we don't delete handlers here — EA owns them
      ArrayResize(m_handlers, 0);
   }

   //--- ICommandDispatcher implementation
   void Register(ICommandHandler *handler) override
   {
      if(handler == NULL) return;
      int size = ArraySize(m_handlers);
      ArrayResize(m_handlers, size + 1);
      m_handlers[size] = handler;
      m_count++;
   }

   bool Dispatch(MessageEnvelope *envelope) override
   {
      if(envelope == NULL)
      {
         if(m_logger != NULL)
            m_logger->Warning("Dispatch called with NULL envelope");
         return false;
      }

      for(int i = 0; i < m_count; i++)
      {
         if(m_handlers[i].CanHandle(envelope.msgType))
         {
            return m_handlers[i].Handle(envelope);
         }
      }

      if(m_logger != NULL)
         m_logger->Warning("No handler registered for msg_type: " + envelope.msgType);
      return false;
   }
};
