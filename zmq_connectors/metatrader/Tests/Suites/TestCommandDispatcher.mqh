//+------------------------------------------------------------------+
//|                       Tests/Suites/TestCommandDispatcher.mqh     |
//|  Suite: CommandDispatcher routes by msg_type to the first        |
//|  handler that claims it, propagates handler results, returns     |
//|  false for unknown types and NULL envelopes.                     |
//+------------------------------------------------------------------+
#property strict

#include "../Framework/TestFramework.mqh"
#include "../Fakes/Fakes.mqh"
#include "../../Application/CommandDispatcher.mqh"

//+------------------------------------------------------------------+
//| FakeCommandHandler — records calls for one claimed msg_type      |
//+------------------------------------------------------------------+
class FakeCommandHandler : public ICommandHandler
{
public:
   string m_handledType;
   int    m_canHandleCalls;
   int    m_handleCalls;
   bool   m_handleResult;
   string m_lastSeenMsgType;

   FakeCommandHandler(string handledType, bool handleResult)
   {
      m_handledType = handledType;
      m_handleResult = handleResult;
      m_canHandleCalls = 0;
      m_handleCalls = 0;
      m_lastSeenMsgType = "";
   }

   bool CanHandle(string msgType) override
   {
      m_canHandleCalls++;
      return (msgType == m_handledType);
   }

   bool Handle(MessageEnvelope *envelope) override
   {
      m_handleCalls++;
      m_lastSeenMsgType = envelope.MsgType();
      return m_handleResult;
   }
};

//+------------------------------------------------------------------+
//| _MakeTypedEnvelope — minimal {"msg_type": ..., "payload": {}}    |
//+------------------------------------------------------------------+
MessageEnvelope *_MakeTypedEnvelope(string msgType)
{
   MessageEnvelope *env = new MessageEnvelope();
   env.root = JSONParser::Parse("{\"msg_type\":\"" + msgType + "\",\"seq_num\":1,\"payload\":{}}");
   return env;
}

//+------------------------------------------------------------------+
//| RunCommandDispatcherTests                                        |
//+------------------------------------------------------------------+
void RunCommandDispatcherTests()
{
   //--- CanHandle matches only the claimed msg_type
   {
      FakeCommandHandler alpha("alpha", true);

      AssertTrue(alpha.CanHandle("alpha"), "Dispatcher: CanHandle true for claimed type");
      AssertFalse(alpha.CanHandle("beta"), "Dispatcher: CanHandle false for other type");
      AssertFalse(alpha.CanHandle(""), "Dispatcher: CanHandle false for empty type");
   }

   //--- Dispatch routes to the correct handler among several
   {
      FakeLogger logger;
      CommandDispatcher dispatcher(GetPointer(logger));
      FakeCommandHandler alpha("alpha", true);
      FakeCommandHandler beta("beta", true);
      dispatcher.Register(GetPointer(alpha));
      dispatcher.Register(GetPointer(beta));

      MessageEnvelope *env = _MakeTypedEnvelope("beta");
      AssertTrue(dispatcher.Dispatch(env), "Dispatcher: routed dispatch returns handler result");
      AssertEqualLong(1, alpha.m_canHandleCalls, "Dispatcher: earlier handler consulted first");
      AssertEqualLong(0, alpha.m_handleCalls, "Dispatcher: non-matching handler not invoked");
      AssertEqualLong(1, beta.m_handleCalls, "Dispatcher: matching handler invoked once");
      AssertEqualString("beta", beta.m_lastSeenMsgType, "Dispatcher: handler saw envelope msg_type");
      AssertEqualLong(0, logger.Count(), "Dispatcher: successful route logs nothing");
      delete env;
   }

   //--- First-registered handler wins for a shared msg_type
   {
      FakeLogger logger;
      CommandDispatcher dispatcher(GetPointer(logger));
      FakeCommandHandler first("dup", true);
      FakeCommandHandler second("dup", true);
      dispatcher.Register(GetPointer(first));
      dispatcher.Register(GetPointer(second));

      MessageEnvelope *env = _MakeTypedEnvelope("dup");
      AssertTrue(dispatcher.Dispatch(env), "Dispatcher: duplicate type dispatched");
      AssertEqualLong(1, first.m_handleCalls, "Dispatcher: first handler wins");
      AssertEqualLong(0, second.m_handleCalls, "Dispatcher: second handler not reached");
      delete env;
   }

   //--- Unknown msg_type → false + warning
   {
      FakeLogger logger;
      CommandDispatcher dispatcher(GetPointer(logger));
      FakeCommandHandler alpha("alpha", true);
      dispatcher.Register(GetPointer(alpha));

      MessageEnvelope *env = _MakeTypedEnvelope("gamma");
      AssertFalse(dispatcher.Dispatch(env), "Dispatcher: unknown type returns false");
      AssertEqualLong(0, alpha.m_handleCalls, "Dispatcher: unknown type invokes no handler");
      AssertTrue(logger.Contains("No handler registered for msg_type: gamma"),
                 "Dispatcher: unknown type logs warning");
      delete env;
   }

   //--- Handler returning false propagates false
   {
      FakeLogger logger;
      CommandDispatcher dispatcher(GetPointer(logger));
      FakeCommandHandler failing("alpha", false);
      dispatcher.Register(GetPointer(failing));

      MessageEnvelope *env = _MakeTypedEnvelope("alpha");
      AssertFalse(dispatcher.Dispatch(env), "Dispatcher: handler false propagates");
      AssertEqualLong(1, failing.m_handleCalls, "Dispatcher: failing handler was invoked");
      delete env;
   }

   //--- NULL envelope → false + warning, no handler touched
   {
      FakeLogger logger;
      CommandDispatcher dispatcher(GetPointer(logger));
      FakeCommandHandler alpha("alpha", true);
      dispatcher.Register(GetPointer(alpha));

      AssertFalse(dispatcher.Dispatch(NULL), "Dispatcher: NULL envelope returns false");
      AssertEqualLong(0, alpha.m_canHandleCalls, "Dispatcher: NULL envelope consults no handler");
      AssertTrue(logger.Contains("NULL envelope"), "Dispatcher: NULL envelope logs warning");
   }

   //--- Register(NULL) is ignored
   {
      FakeLogger logger;
      CommandDispatcher dispatcher(GetPointer(logger));
      dispatcher.Register(NULL);

      MessageEnvelope *env = _MakeTypedEnvelope("alpha");
      AssertFalse(dispatcher.Dispatch(env), "Dispatcher: NULL handler registration ignored");
      delete env;
   }

   //--- NULL logger tolerated
   {
      CommandDispatcher dispatcher(NULL);
      FakeCommandHandler alpha("alpha", true);
      dispatcher.Register(GetPointer(alpha));

      MessageEnvelope *env = _MakeTypedEnvelope("alpha");
      AssertTrue(dispatcher.Dispatch(env), "Dispatcher: works with NULL logger");
      delete env;

      env = _MakeTypedEnvelope("unknown");
      AssertFalse(dispatcher.Dispatch(env), "Dispatcher: unknown type false with NULL logger");
      delete env;
   }
}
//+------------------------------------------------------------------+
