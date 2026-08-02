//+------------------------------------------------------------------+
//|                             Commands/RefreshRequestHandler.mqh   |
//|  Handles refresh_request command: resend historical bars.        |
//|  Uses the required 'instrument' payload field (not the chart).   |
//+------------------------------------------------------------------+
#property strict

#include "../Domain/Contracts.mqh"
#include "../Domain/MessageTypes.mqh"
#include "../Domain/ValueObjects.mqh"
#include "../Domain/PlatformApi.mqh"

//+------------------------------------------------------------------+
//| RefreshRequestHandler — triggers history resend                  |
//+------------------------------------------------------------------+
class RefreshRequestHandler : public ICommandHandler
{
private:
   IZmqNetwork      *m_network;
   ILogger          *m_logger;
   IHistoryProvider *m_historyProvider;
   PlatformApis     *m_apis;

public:
   RefreshRequestHandler(IZmqNetwork *network, ILogger *logger, IHistoryProvider *historyProvider, PlatformApis *apis)
   {
      m_network = network;
      m_logger = logger;
      m_historyProvider = historyProvider;
      m_apis = apis;
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
      string instrument = envelope.PayloadString("instrument");

      // instrument is REQUIRED — refresh targets the given broker symbol
      if(StringLen(instrument) == 0)
      {
         if(m_logger != NULL)
            m_logger.Error("RefreshRequestHandler: instrument is required");
         m_network.SendError("metatrader5", "refresh_failed", "instrument is required in refresh_request payload");
         return false;
      }

      if(!m_apis.symbol.Select(instrument))
      {
         if(m_logger != NULL)
            m_logger.Error("RefreshRequestHandler: unknown symbol '" + instrument + "'");
         m_network.SendError("metatrader5", "refresh_failed", "unknown symbol: " + instrument);
         return false;
      }

      if(m_logger != NULL)
         m_logger.Info("Refresh request received — resending history for " + instrument + (days > 0 ? " (days=" + IntegerToString(days) + ")" : ""));

      if(m_historyProvider != NULL)
         m_historyProvider.SendHistory(days, instrument);
      else if(m_logger != NULL)
         m_logger.Warning("RefreshRequestHandler: no history provider available");

      return true;
   }
};
