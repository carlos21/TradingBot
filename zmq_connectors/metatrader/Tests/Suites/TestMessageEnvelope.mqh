//+------------------------------------------------------------------+
//|                         Tests/Suites/TestMessageEnvelope.mqh     |
//|  Suite: MessageEnvelope accessors read the parsed JSON tree,     |
//|  fall back to caller defaults on missing payload/keys, and       |
//|  coerce across JSON value types.                                 |
//+------------------------------------------------------------------+
#property strict

#include "../Framework/TestFramework.mqh"
#include "../../Domain/ValueObjects.mqh"

//+------------------------------------------------------------------+
//| RunMessageEnvelopeTests                                          |
//+------------------------------------------------------------------+
void RunMessageEnvelopeTests()
{
   //--- Full envelope: every accessor reads its field
   {
      MessageEnvelope env;
      env.root = JSONParser::Parse(
         "{\"msg_type\":\"subscribe\",\"timestamp\":1700000000.5,\"seq_num\":42,"
         "\"payload\":{\"instrument\":\"EURUSD\",\"volume\":1.25,\"count\":7,\"active\":true}}");

      AssertEqualString("subscribe", env.MsgType(), "Envelope: MsgType reads msg_type");
      AssertEqualDouble(1700000000.5, env.Timestamp(), 0.001, "Envelope: Timestamp reads timestamp");
      AssertEqualLong(42, env.SeqNum(), "Envelope: SeqNum reads seq_num");
      AssertEqualString("EURUSD", env.PayloadString("instrument"), "Envelope: PayloadString reads string");
      AssertEqualDouble(1.25, env.PayloadDouble("volume"), 0.0001, "Envelope: PayloadDouble reads number");
      AssertEqualLong(7, env.PayloadInt("count"), "Envelope: PayloadInt reads int");
      AssertTrue(env.PayloadBool("active"), "Envelope: PayloadBool reads true");
      AssertTrue(env.PayloadHasKey("instrument"), "Envelope: PayloadHasKey true for present key");
      AssertFalse(env.PayloadHasKey("missing"), "Envelope: PayloadHasKey false for absent key");
   }

   //--- Explicit false bool beats a true default
   {
      MessageEnvelope env;
      env.root = JSONParser::Parse("{\"msg_type\":\"x\",\"payload\":{\"active\":false}}");

      AssertFalse(env.PayloadBool("active"), "Envelope: PayloadBool reads false");
      AssertFalse(env.PayloadBool("active", true), "Envelope: stored false overrides true default");
   }

   //--- Default-constructed envelope (root == NULL): all fallbacks
   {
      MessageEnvelope env;

      AssertEqualString("", env.MsgType(), "Envelope: NULL root MsgType empty");
      AssertEqualDouble(0.0, env.Timestamp(), 0.0001, "Envelope: NULL root Timestamp zero");
      AssertEqualLong(0, env.SeqNum(), "Envelope: NULL root SeqNum zero");
      AssertEqualString("dflt", env.PayloadString("k", "dflt"), "Envelope: NULL root PayloadString default");
      AssertEqualDouble(9.5, env.PayloadDouble("k", 9.5), 0.0001, "Envelope: NULL root PayloadDouble default");
      AssertEqualLong(-3, env.PayloadInt("k", -3), "Envelope: NULL root PayloadInt default");
      AssertTrue(env.PayloadBool("k", true), "Envelope: NULL root PayloadBool default");
      AssertFalse(env.PayloadHasKey("k"), "Envelope: NULL root PayloadHasKey false");
   }

   //--- Parsed object without a payload key: payload accessors default
   {
      MessageEnvelope env;
      env.root = JSONParser::Parse("{\"msg_type\":\"ping\",\"timestamp\":3,\"seq_num\":9}");

      AssertEqualString("ping", env.MsgType(), "Envelope: no-payload MsgType still reads");
      AssertEqualString("dflt", env.PayloadString("k", "dflt"), "Envelope: no-payload PayloadString default");
      AssertEqualDouble(2.5, env.PayloadDouble("k", 2.5), 0.0001, "Envelope: no-payload PayloadDouble default");
      AssertEqualLong(11, env.PayloadInt("k", 11), "Envelope: no-payload PayloadInt default");
      AssertTrue(env.PayloadBool("k", true), "Envelope: no-payload PayloadBool default");
      AssertFalse(env.PayloadHasKey("k"), "Envelope: no-payload PayloadHasKey false");
   }

   //--- Payload present but requested key missing: defaults returned
   {
      MessageEnvelope env;
      env.root = JSONParser::Parse("{\"payload\":{\"instrument\":\"EURUSD\"}}");

      AssertEqualString("fallback", env.PayloadString("nope", "fallback"), "Envelope: missing key PayloadString default");
      AssertEqualDouble(2.5, env.PayloadDouble("nope", 2.5), 0.0001, "Envelope: missing key PayloadDouble default");
      AssertEqualLong(11, env.PayloadInt("nope", 11), "Envelope: missing key PayloadInt default");
      AssertTrue(env.PayloadBool("nope", true), "Envelope: missing key PayloadBool default");
   }

   //--- Implicit default arguments are empty/zero/false
   {
      MessageEnvelope env;
      env.root = JSONParser::Parse("{\"payload\":{\"instrument\":\"EURUSD\"}}");

      AssertEqualString("", env.PayloadString("nope"), "Envelope: implicit PayloadString default empty");
      AssertEqualDouble(0.0, env.PayloadDouble("nope"), 0.0001, "Envelope: implicit PayloadDouble default zero");
      AssertEqualLong(0, env.PayloadInt("nope"), "Envelope: implicit PayloadInt default zero");
      AssertFalse(env.PayloadBool("nope"), "Envelope: implicit PayloadBool default false");
   }

   //--- Missing top-level keys on a parsed object
   {
      MessageEnvelope env;
      env.root = JSONParser::Parse("{\"payload\":{}}");

      AssertEqualString("", env.MsgType(), "Envelope: missing msg_type empty");
      AssertEqualDouble(0.0, env.Timestamp(), 0.0001, "Envelope: missing timestamp zero");
      AssertEqualLong(0, env.SeqNum(), "Envelope: missing seq_num zero");
      AssertFalse(env.PayloadHasKey("x"), "Envelope: empty payload has no keys");
   }

   //--- Type coercion across JSON value kinds
   {
      MessageEnvelope env;
      env.root = JSONParser::Parse(
         "{\"msg_type\":123,\"timestamp\":5,\"seq_num\":7.9,"
         "\"payload\":{\"count\":7,\"ratio\":3.9,\"flag\":true,\"name\":1.25}}");

      AssertEqualString("123", env.MsgType(), "Envelope: number msg_type coerces to string");
      AssertEqualDouble(5.0, env.Timestamp(), 0.0001, "Envelope: int timestamp coerces to double");
      AssertEqualLong(7, env.SeqNum(), "Envelope: double seq_num truncates to long");
      AssertEqualDouble(7.0, env.PayloadDouble("count"), 0.0001, "Envelope: int coerces to double");
      AssertEqualLong(3, env.PayloadInt("ratio"), "Envelope: double payload truncates to int");
      AssertEqualString("7", env.PayloadString("count"), "Envelope: number coerces to string");
      AssertEqualString("true", env.PayloadString("flag"), "Envelope: bool coerces to string");
      AssertEqualString("1.25000000", env.PayloadString("name"), "Envelope: fractional number coerces to string");
   }
}
//+------------------------------------------------------------------+
