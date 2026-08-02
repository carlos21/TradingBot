//+------------------------------------------------------------------+
//|                                 Tests/Fakes/FakeTradeApi.mqh     |
//|  Fake ITradeApi — replays a queue of TradeResult and records     |
//|  the last MqlTradeRequest for assertions.                        |
//+------------------------------------------------------------------+
#property strict

#include "../../Domain/PlatformApi.mqh"

//+------------------------------------------------------------------+
//| FakeTradeApi — scripted trade-server responses                   |
//+------------------------------------------------------------------+
class FakeTradeApi : public ITradeApi
{
private:
   TradeResult m_queue[];
   int         m_queueIndex;
   TradeResult m_defaultResult;

   //--- Last request snapshot (MqlTradeRequest is not copyable into
   //--- a plain struct field cheaply; store the interesting fields).
   bool   m_hasRequest;
   int    m_lastAction;
   string m_lastSymbol;
   double m_lastVolume;
   double m_lastPrice;
   double m_lastSl;
   double m_lastTp;
   long   m_lastType;
   long   m_lastMagic;
   string m_lastComment;
   ulong  m_lastPosition;
   int    m_sendCount;

   //--- Optional override of the Send() return value. Real OrderSend
   //--- can return true with a rejection retcode (and vice versa);
   //--- this hook lets tests drive the handlers into those branches.
   bool   m_forceSend;
   bool   m_forcedSendValue;

public:
   FakeTradeApi()
   {
      m_queueIndex = 0;
      m_defaultResult.retcode = 10009; //--- TRADE_RETCODE_DONE
      m_defaultResult.order = 0;
      m_defaultResult.price = 0.0;
      m_defaultResult.volume = 0.0;
      m_defaultResult.error = 0;
      m_hasRequest = false;
      m_sendCount = 0;
      m_lastPosition = 0;
      m_forceSend = false;
      m_forcedSendValue = true;
   }

   //--- Test hooks -------------------------------------------------
   void QueueResult(uint retcode, ulong order, double price, double volume)
   {
      int n = ArraySize(m_queue);
      ArrayResize(m_queue, n + 1);
      m_queue[n].retcode = retcode;
      m_queue[n].order = order;
      m_queue[n].price = price;
      m_queue[n].volume = volume;
      m_queue[n].error = 0;
   }

   void SetDefaultRetcode(uint retcode) { m_defaultResult.retcode = retcode; }

   int    SendCount()     { return m_sendCount; }
   bool   HasRequest()    { return m_hasRequest; }
   int    LastAction()    { return m_lastAction; }
   string LastSymbol()    { return m_lastSymbol; }
   double LastVolume()    { return m_lastVolume; }
   double LastPrice()     { return m_lastPrice; }
   double LastStopLoss()  { return m_lastSl; }
   double LastTakeProfit(){ return m_lastTp; }
   long   LastType()      { return m_lastType; }
   long   LastMagic()     { return m_lastMagic; }
   string LastComment()   { return m_lastComment; }
   ulong  LastPosition()  { return m_lastPosition; }

   //--- Arm/disarm a forced Send() return value. When armed, Send()
   //--- returns the forced value regardless of the queued retcode.
   void ForceSendReturn(bool value) { m_forceSend = true; m_forcedSendValue = value; }
   void ClearForceSendReturn()      { m_forceSend = false; }

   //--- ITradeApi --------------------------------------------------
   bool Send(MqlTradeRequest &request, TradeResult &result) override
   {
      m_sendCount++;
      m_hasRequest = true;
      m_lastAction = (int)request.action;
      m_lastSymbol = request.symbol;
      m_lastVolume = request.volume;
      m_lastPrice = request.price;
      m_lastSl = request.sl;
      m_lastTp = request.tp;
      m_lastType = (long)request.type;
      m_lastMagic = (long)request.magic;
      m_lastComment = request.comment;
      m_lastPosition = request.position;

      if(m_queueIndex < ArraySize(m_queue))
         result = m_queue[m_queueIndex++];
      else
         result = m_defaultResult;
      //--- Forced return takes precedence over retcode mirroring.
      if(m_forceSend)
         return m_forcedSendValue;
      //--- Mirror OrderSend semantics: retcode DONE / DONE_PARTIAL
      //--- means the server accepted the request.
      return result.retcode == 10009 || result.retcode == 10010;
   }
};
