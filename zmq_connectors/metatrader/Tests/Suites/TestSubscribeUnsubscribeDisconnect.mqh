//+------------------------------------------------------------------+
//|              Tests/Suites/TestSubscribeUnsubscribeDisconnect.mqh |
//|  Suite: SubscribeHandler / UnsubscribeHandler / DisconnectHandler|
//|  drive the SubscriptionManager and record only expected sends    |
//|  on the network fake.                                            |
//+------------------------------------------------------------------+
#property strict

#include "../Framework/TestFramework.mqh"
#include "../Fakes/Fakes.mqh"
#include "../../Application/SubscriptionManager.mqh"

//--- The EA-owned global the DisconnectHandler stamps
//--- (declared in TradingBotZmqEA.mq5:92). Re-declared here so the
//--- handler links inside the test harness.
long g_disconnectRequestTick = 0;

#include "../../Commands/SubscribeHandler.mqh"
#include "../../Commands/UnsubscribeHandler.mqh"
#include "../../Commands/DisconnectHandler.mqh"

//+------------------------------------------------------------------+
//| _MakeCmdEnvelope — {"msg_type": ..., "payload": <payloadJson>}   |
//+------------------------------------------------------------------+
MessageEnvelope *_MakeCmdEnvelope(string msgType, string payloadJson)
{
   MessageEnvelope *env = new MessageEnvelope();
   env.root = JSONParser::Parse(
      "{\"msg_type\":\"" + msgType + "\",\"seq_num\":1,\"payload\":" + payloadJson + "}");
   return env;
}

//+------------------------------------------------------------------+
//| RunSubscribeUnsubscribeDisconnectTests                           |
//+------------------------------------------------------------------+
void RunSubscribeUnsubscribeDisconnectTests()
{
   //--- SubscribeHandler: CanHandle claims only "subscribe"
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeSymbolApi symbols;
      FakeTimeApi time;
      SubscriptionManager subs(GetPointer(logger), 5, GetPointer(symbols), GetPointer(time));
      SubscribeHandler handler(GetPointer(network), GetPointer(logger), GetPointer(subs));

      AssertTrue(handler.CanHandle(MT_SUBSCRIBE), "Subscribe: CanHandle subscribe");
      AssertFalse(handler.CanHandle(MT_UNSUBSCRIBE), "Subscribe: CanHandle rejects unsubscribe");
      AssertFalse(handler.CanHandle(MT_DISCONNECT), "Subscribe: CanHandle rejects disconnect");
   }

   //--- SubscribeHandler: valid subscribe adds the symbol, sends nothing
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeSymbolApi symbols;
      FakeTimeApi time;
      SubscriptionManager subs(GetPointer(logger), 5, GetPointer(symbols), GetPointer(time));
      SubscribeHandler handler(GetPointer(network), GetPointer(logger), GetPointer(subs));

      MessageEnvelope *env = _MakeCmdEnvelope(MT_SUBSCRIBE, "{\"instrument\":\"EURUSD\"}");
      AssertTrue(handler.Handle(env), "Subscribe: valid instrument returns true (ack-positive)");
      AssertTrue(subs.Contains("EURUSD"), "Subscribe: symbol added to manager");
      AssertEqualLong(0, network.SentCount(), "Subscribe: success sends nothing");
      delete env;

      //--- Re-subscribe is idempotent
      env = _MakeCmdEnvelope(MT_SUBSCRIBE, "{\"instrument\":\"EURUSD\"}");
      AssertTrue(handler.Handle(env), "Subscribe: re-subscribe returns true");
      AssertEqualLong(1, subs.Count(), "Subscribe: re-subscribe keeps count");
      delete env;
   }

   //--- SubscribeHandler: missing instrument → false + subscribe_failed error
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeSymbolApi symbols;
      FakeTimeApi time;
      SubscriptionManager subs(GetPointer(logger), 5, GetPointer(symbols), GetPointer(time));
      SubscribeHandler handler(GetPointer(network), GetPointer(logger), GetPointer(subs));

      MessageEnvelope *env = _MakeCmdEnvelope(MT_SUBSCRIBE, "{}");
      AssertFalse(handler.Handle(env), "Subscribe: missing instrument returns false");
      int errIdx = network.FindByMsgType(MT_ERROR);
      AssertTrue(errIdx >= 0, "Subscribe: error sent for missing instrument");
      AssertTrue(network.PayloadAtContains(errIdx, "subscribe_failed"), "Subscribe: error type recorded");
      AssertTrue(network.PayloadAtContains(errIdx, "instrument is required"), "Subscribe: error message recorded");
      AssertEqualLong(0, subs.Count(), "Subscribe: nothing subscribed on failure");
      delete env;
   }

   //--- SubscribeHandler: unknown symbol → false + unknown symbol error
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeSymbolApi symbols;
      FakeTimeApi time;
      symbols.SetSelectResult("NOPE", false);
      SubscriptionManager subs(GetPointer(logger), 5, GetPointer(symbols), GetPointer(time));
      SubscribeHandler handler(GetPointer(network), GetPointer(logger), GetPointer(subs));

      MessageEnvelope *env = _MakeCmdEnvelope(MT_SUBSCRIBE, "{\"instrument\":\"NOPE\"}");
      AssertFalse(handler.Handle(env), "Subscribe: unknown symbol returns false");
      int errIdx = network.FindByMsgType(MT_ERROR);
      AssertTrue(errIdx >= 0, "Subscribe: error sent for unknown symbol");
      AssertTrue(network.PayloadAtContains(errIdx, "unknown symbol"), "Subscribe: unknown-symbol message recorded");
      AssertFalse(subs.Contains("NOPE"), "Subscribe: unknown symbol not tracked");
      delete env;
   }

   //--- SubscribeHandler: NULL / rootless envelope → false, nothing sent
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeSymbolApi symbols;
      FakeTimeApi time;
      SubscriptionManager subs(GetPointer(logger), 5, GetPointer(symbols), GetPointer(time));
      SubscribeHandler handler(GetPointer(network), GetPointer(logger), GetPointer(subs));

      AssertFalse(handler.Handle(NULL), "Subscribe: NULL envelope returns false");
      AssertTrue(logger.Contains("SubscribeHandler: empty envelope"), "Subscribe: NULL envelope logged");
      AssertEqualLong(0, network.SentCount(), "Subscribe: NULL envelope sends nothing");

      MessageEnvelope broken;   //--- root == NULL
      AssertFalse(handler.Handle(GetPointer(broken)), "Subscribe: rootless envelope returns false");
      AssertEqualLong(0, network.SentCount(), "Subscribe: rootless envelope sends nothing");
   }

   //--- UnsubscribeHandler: CanHandle claims only "unsubscribe"
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeSymbolApi symbols;
      FakeTimeApi time;
      SubscriptionManager subs(GetPointer(logger), 5, GetPointer(symbols), GetPointer(time));
      UnsubscribeHandler handler(GetPointer(network), GetPointer(logger), GetPointer(subs));

      AssertTrue(handler.CanHandle(MT_UNSUBSCRIBE), "Unsubscribe: CanHandle unsubscribe");
      AssertFalse(handler.CanHandle(MT_SUBSCRIBE), "Unsubscribe: CanHandle rejects subscribe");
   }

   //--- UnsubscribeHandler: removes the symbol, sends nothing
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeSymbolApi symbols;
      FakeTimeApi time;
      SubscriptionManager subs(GetPointer(logger), 5, GetPointer(symbols), GetPointer(time));
      UnsubscribeHandler handler(GetPointer(network), GetPointer(logger), GetPointer(subs));
      subs.Add("EURUSD");

      MessageEnvelope *env = _MakeCmdEnvelope(MT_UNSUBSCRIBE, "{\"instrument\":\"EURUSD\"}");
      AssertTrue(handler.Handle(env), "Unsubscribe: known symbol returns true (ack-positive)");
      AssertFalse(subs.Contains("EURUSD"), "Unsubscribe: symbol removed from manager");
      AssertEqualLong(0, network.SentCount(), "Unsubscribe: success sends nothing");
      delete env;

      //--- Unknown symbol is idempotent-success
      env = _MakeCmdEnvelope(MT_UNSUBSCRIBE, "{\"instrument\":\"AUDUSD\"}");
      AssertTrue(handler.Handle(env), "Unsubscribe: unknown symbol still returns true");
      AssertEqualLong(0, network.SentCount(), "Unsubscribe: unknown symbol sends nothing");
      delete env;
   }

   //--- UnsubscribeHandler: missing instrument → false + error
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      FakeSymbolApi symbols;
      FakeTimeApi time;
      SubscriptionManager subs(GetPointer(logger), 5, GetPointer(symbols), GetPointer(time));
      UnsubscribeHandler handler(GetPointer(network), GetPointer(logger), GetPointer(subs));

      MessageEnvelope *env = _MakeCmdEnvelope(MT_UNSUBSCRIBE, "{}");
      AssertFalse(handler.Handle(env), "Unsubscribe: missing instrument returns false");
      int errIdx = network.FindByMsgType(MT_ERROR);
      AssertTrue(errIdx >= 0, "Unsubscribe: error sent for missing instrument");
      AssertTrue(network.PayloadAtContains(errIdx, "unsubscribe_failed"), "Unsubscribe: error type recorded");
      AssertTrue(network.PayloadAtContains(errIdx, "instrument is required"), "Unsubscribe: error message recorded");
      delete env;

      AssertFalse(handler.Handle(NULL), "Unsubscribe: NULL envelope returns false");
      AssertTrue(logger.Contains("UnsubscribeHandler: empty envelope"), "Unsubscribe: NULL envelope logged");
   }

   //--- DisconnectHandler: CanHandle claims only "disconnect"
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      DisconnectHandler handler(GetPointer(network), GetPointer(logger));

      AssertTrue(handler.CanHandle(MT_DISCONNECT), "Disconnect: CanHandle disconnect");
      AssertFalse(handler.CanHandle(MT_SUBSCRIBE), "Disconnect: CanHandle rejects subscribe");
   }

   //--- DisconnectHandler: stamps the deferred-teardown tick, sends nothing
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      DisconnectHandler handler(GetPointer(network), GetPointer(logger));

      g_disconnectRequestTick = 0;
      MessageEnvelope *env = _MakeCmdEnvelope(MT_DISCONNECT, "{\"reason\":\"test stop\"}");
      AssertTrue(handler.Handle(env), "Disconnect: valid command returns true (ack-positive)");
      AssertTrue(g_disconnectRequestTick != 0, "Disconnect: request tick stamped");
      AssertTrue(logger.Contains("test stop"), "Disconnect: reason logged");
      AssertEqualLong(0, network.SentCount(), "Disconnect: handler sends nothing");
      delete env;
   }

   //--- DisconnectHandler: missing reason falls back to a default
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      DisconnectHandler handler(GetPointer(network), GetPointer(logger));

      g_disconnectRequestTick = 0;
      MessageEnvelope *env = _MakeCmdEnvelope(MT_DISCONNECT, "{}");
      AssertTrue(handler.Handle(env), "Disconnect: reasonless command returns true");
      AssertTrue(g_disconnectRequestTick != 0, "Disconnect: tick stamped without reason");
      AssertTrue(logger.Contains("stream stopped"), "Disconnect: default reason logged");
      delete env;
   }

   //--- DisconnectHandler: NULL envelope → false, tick untouched
   {
      FakeZmqNetwork network;
      FakeLogger logger;
      DisconnectHandler handler(GetPointer(network), GetPointer(logger));

      g_disconnectRequestTick = 0;
      AssertFalse(handler.Handle(NULL), "Disconnect: NULL envelope returns false");
      AssertEqualLong(0, g_disconnectRequestTick, "Disconnect: NULL envelope leaves tick unset");
      AssertTrue(logger.Contains("DisconnectHandler: empty envelope"), "Disconnect: NULL envelope logged");
      AssertEqualLong(0, network.SentCount(), "Disconnect: NULL envelope sends nothing");
   }
}
//+------------------------------------------------------------------+
