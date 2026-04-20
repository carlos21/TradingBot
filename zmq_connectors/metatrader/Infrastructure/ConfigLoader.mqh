//+------------------------------------------------------------------+
//|                                Infrastructure/ConfigLoader.mqh   |
//|  Loads TradingBotZmqConfig.json from MQL5 Files directory.       |
//+------------------------------------------------------------------+
#property strict

#include "../Domain/ValueObjects.mqh"
#include <JSON/JSON.mqh>
#include <Files/FileTxt.mqh>

//+------------------------------------------------------------------+
//| ConfigLoader — reads JSON config into ZmqConfiguration           |
//+------------------------------------------------------------------+
class ConfigLoader
{
public:
   //--- Load config from file, or return defaults if missing
   static ZmqConfiguration Load(string filename = "TradingBotZmqConfig.json")
   {
      ZmqConfiguration cfg;

      string filepath = filename;
      int handle = FileOpen(filepath, FILE_READ|FILE_TXT|FILE_COMMON);
      if(handle == INVALID_HANDLE)
      {
         // Try without FILE_COMMON (in MQL5/Files/)
         handle = FileOpen(filepath, FILE_READ|FILE_TXT);
      }

      if(handle == INVALID_HANDLE)
      {
         Print("[ConfigLoader] Config file not found: ", filename, ". Using defaults.");
         return cfg;
      }

      string json = "";
      while(!FileIsEnding(handle))
      {
         json += FileReadString(handle);
      }
      FileClose(handle);

      JSONValue *root = JSONParser::Parse(json);
      if(root == NULL || !root.IsObject())
      {
         Print("[ConfigLoader] Failed to parse config JSON. Using defaults.");
         if(root != NULL) delete root;
         return cfg;
      }

      // Helper lambda would be nice, but MQL5 doesn't support lambdas well
      // So we read each field manually
      if(root.HasKey("host"))       cfg.host       = root["host"].ToString();
      if(root.HasKey("marketPort")) cfg.marketPort = (int)root["marketPort"].ToInt();
      if(root.HasKey("commandPort")) cfg.commandPort = (int)root["commandPort"].ToInt();
      if(root.HasKey("queryPort"))  cfg.queryPort  = (int)root["queryPort"].ToInt();
      if(root.HasKey("heartbeatPort")) cfg.heartbeatPort = (int)root["heartbeatPort"].ToInt();
      if(root.HasKey("pair"))       cfg.pair       = root["pair"].ToString();
      if(root.HasKey("historyDays")) cfg.historyDays = (int)root["historyDays"].ToInt();
      if(root.HasKey("heartbeatSec")) cfg.heartbeatSec = (int)root["heartbeatSec"].ToInt();
      if(root.HasKey("magicNumber")) cfg.magicNumber = (ulong)root["magicNumber"].ToInt();
      if(root.HasKey("maxTicksPerSecond")) cfg.maxTicksPerSecond = (int)root["maxTicksPerSecond"].ToInt();
      if(root.HasKey("batchSize"))  cfg.batchSize  = (int)root["batchSize"].ToInt();
      if(root.HasKey("autoConnectOnStartup")) cfg.autoConnectOnStartup = root["autoConnectOnStartup"].ToBool();
      if(root.HasKey("autoShowPanel")) cfg.autoShowPanel = root["autoShowPanel"].ToBool();
      if(root.HasKey("platformVersion")) cfg.platformVersion = root["platformVersion"].ToString();

      delete root;
      Print("[ConfigLoader] Config loaded from ", filename);
      return cfg;
   }
};
