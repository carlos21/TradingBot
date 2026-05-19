//+------------------------------------------------------------------+
//|                             Commands/RefreshRequestHandler.mqh   |
//|  Handles refresh_request command: resend historical bars.        |
//+------------------------------------------------------------------+
#property strict

#include "../Domain/Contracts.mqh"
#include "../Domain/MessageTypes.mqh"

//+------------------------------------------------------------------+
//| RefreshRequestHandler — triggers history resend                  |
//+------------------------------------------------------------------+
class RefreshRequestHandler : public ICommandHandler
{
private:
   IZmqNetwork      *m_network;
   ILogger          *m_logger;
   IHistoryProvider *m_historyProvider;

public:
   RefreshRequestHandler(IZmqNetwork *network, ILogger *logger, IHistoryProvider *historyProvider)
   {
      m_network = network;
      m_logger = logger;
      m_historyProvider = historyProvider;
   }

   ~RefreshRequestHandler() {}

   //--- ICommandHandler implementation
   bool CanHandle(string msgType) override
   {
      return (msgType == MT_REFRESH_REQUEST);
   }

   bool Handle(MessageEnvelope *envelope) override
   {
      int days = envelope.PayloadInt("days", 0);
      if(m_logger != NULL)
         m_logger.Info("Refresh request received — resending history" + (days > 0 ? " (days=" + IntegerToString(days) + ")" : ""));

      if(m_historyProvider != NULL)
         m_historyProvider.SendHistory(days);
      else if(m_logger != NULL)
         m_logger.Warning("RefreshRequestHandler: no history provider available");

      return true;
   }
};
