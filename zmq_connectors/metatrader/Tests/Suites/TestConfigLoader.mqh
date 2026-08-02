//+------------------------------------------------------------------+
//|                          Tests/Suites/TestConfigLoader.mqh       |
//|  Suite: ConfigLoader::Parse — full JSON, defaults on missing     |
//|  keys, string-fallback on malformed JSON, defaults when nothing  |
//|  is recoverable, and bool/int coercion. Pure (no file I/O).      |
//+------------------------------------------------------------------+
#property strict

#include "../Framework/TestFramework.mqh"
#include "../../Infrastructure/ConfigLoader.mqh"

//+------------------------------------------------------------------+
//| RunConfigLoaderTests                                             |
//+------------------------------------------------------------------+
void RunConfigLoaderTests()
{
   //--- Full JSON: every key parsed (accountName present but ignored —
   //--- ZmqConfiguration no longer carries such a field)
   {
      string json = "{"
         "\"host\":\"192.168.1.50\","
         "\"marketPort\":6601,\"commandPort\":6602,\"queryPort\":6603,\"heartbeatPort\":6604,"
         "\"pair\":\"USDJPY\",\"historyDays\":30,\"heartbeatSec\":15,"
         "\"magicNumber\":777,\"maxTicksPerSecond\":25,\"batchSize\":1000,"
         "\"autoConnectOnStartup\":false,\"autoShowPanel\":false,\"simulateTrades\":true,"
         "\"platformVersion\":\"9.9.9\","
         "\"accountName\":\"12345678\""
         "}";

      ZmqConfiguration cfg = ConfigLoader::Parse(json, "test-full");

      AssertEqualString("192.168.1.50", cfg.host, "Config: full host parsed");
      AssertEqualLong(6601, cfg.marketPort, "Config: full marketPort parsed");
      AssertEqualLong(6602, cfg.commandPort, "Config: full commandPort parsed");
      AssertEqualLong(6603, cfg.queryPort, "Config: full queryPort parsed");
      AssertEqualLong(6604, cfg.heartbeatPort, "Config: full heartbeatPort parsed");
      AssertEqualString("USDJPY", cfg.pair, "Config: full pair parsed");
      AssertEqualLong(30, cfg.historyDays, "Config: full historyDays parsed");
      AssertEqualLong(15, cfg.heartbeatSec, "Config: full heartbeatSec parsed");
      AssertEqualLong(777, (long)cfg.magicNumber, "Config: full magicNumber parsed");
      AssertEqualLong(25, cfg.maxTicksPerSecond, "Config: full maxTicksPerSecond parsed");
      AssertEqualLong(1000, cfg.batchSize, "Config: full batchSize parsed");
      AssertFalse(cfg.autoConnectOnStartup, "Config: full autoConnectOnStartup parsed");
      AssertFalse(cfg.autoShowPanel, "Config: full autoShowPanel parsed");
      AssertTrue(cfg.simulateTrades, "Config: full simulateTrades parsed");
      AssertEqualString("9.9.9", cfg.platformVersion, "Config: full platformVersion parsed");
      //--- accountName is not part of the config schema anymore; its
      //--- presence must not disturb parsing of the real keys above.
      AssertEqualString("192.168.1.50", cfg.host, "Config: accountName key ignored");
   }

   //--- Missing keys: defaults preserved
   {
      ZmqConfiguration cfg = ConfigLoader::Parse("{\"host\":\"10.1.1.1\"}", "test-partial");

      AssertEqualString("10.1.1.1", cfg.host, "Config: partial host parsed");
      AssertEqualLong(5565, cfg.marketPort, "Config: default marketPort preserved");
      AssertEqualLong(5566, cfg.commandPort, "Config: default commandPort preserved");
      AssertEqualLong(5567, cfg.queryPort, "Config: default queryPort preserved");
      AssertEqualLong(5568, cfg.heartbeatPort, "Config: default heartbeatPort preserved");
      AssertEqualString("EURUSD", cfg.pair, "Config: default pair preserved");
      AssertEqualLong(1, cfg.historyDays, "Config: default historyDays preserved");
      AssertEqualLong(5, cfg.heartbeatSec, "Config: default heartbeatSec preserved");
      AssertEqualLong(424242, (long)cfg.magicNumber, "Config: default magicNumber preserved");
      AssertEqualLong(10, cfg.maxTicksPerSecond, "Config: default maxTicksPerSecond preserved");
      AssertEqualLong(500, cfg.batchSize, "Config: default batchSize preserved");
      AssertTrue(cfg.autoConnectOnStartup, "Config: default autoConnectOnStartup preserved");
      AssertTrue(cfg.autoShowPanel, "Config: default autoShowPanel preserved");
      AssertFalse(cfg.simulateTrades, "Config: default simulateTrades preserved");
      AssertEqualString("3.0.0", cfg.platformVersion, "Config: default platformVersion preserved");
   }

   //--- Malformed JSON with a recoverable host: string-fallback path
   {
      string broken = "garbage <<< \"host\" : \"10.0.0.9\", \"marketPort\" : 6000, \"simulateTrades\" : true >>>";
      ZmqConfiguration cfg = ConfigLoader::Parse(broken, "test-fallback");

      AssertEqualString("10.0.0.9", cfg.host, "Config: fallback host extracted");
      AssertEqualLong(6000, cfg.marketPort, "Config: fallback marketPort extracted");
      AssertTrue(cfg.simulateTrades, "Config: fallback simulateTrades extracted");
      AssertEqualLong(5566, cfg.commandPort, "Config: fallback keeps default commandPort");
      AssertTrue(cfg.autoConnectOnStartup, "Config: fallback keeps default autoConnectOnStartup");
   }

   //--- Totally unparseable (no host anywhere): pure defaults
   {
      ZmqConfiguration cfg = ConfigLoader::Parse("not json at all", "test-garbage");

      AssertEqualString("127.0.0.1", cfg.host, "Config: garbage keeps default host");
      AssertEqualLong(5565, cfg.marketPort, "Config: garbage keeps default marketPort");
      AssertEqualString("EURUSD", cfg.pair, "Config: garbage keeps default pair");
      AssertEqualString("3.0.0", cfg.platformVersion, "Config: garbage keeps default platformVersion");
   }

   //--- Valid JSON but not an object: fallback finds no host → defaults
   {
      ZmqConfiguration cfg = ConfigLoader::Parse("[1,2,3]", "test-array");

      AssertEqualString("127.0.0.1", cfg.host, "Config: array root keeps default host");
      AssertEqualLong(5565, cfg.marketPort, "Config: array root keeps default marketPort");
   }

   //--- Coercion: fractional JSON numbers truncate to int, bools apply
   {
      string json = "{\"historyDays\":7.0,\"maxTicksPerSecond\":15,"
                    "\"autoConnectOnStartup\":false,\"autoShowPanel\":true}";
      ZmqConfiguration cfg = ConfigLoader::Parse(json, "test-coercion");

      AssertEqualLong(7, cfg.historyDays, "Config: fractional number coerces to int");
      AssertEqualLong(15, cfg.maxTicksPerSecond, "Config: int value parsed");
      AssertFalse(cfg.autoConnectOnStartup, "Config: false bool overrides true default");
      AssertTrue(cfg.autoShowPanel, "Config: true bool parsed");
   }
}
//+------------------------------------------------------------------+
