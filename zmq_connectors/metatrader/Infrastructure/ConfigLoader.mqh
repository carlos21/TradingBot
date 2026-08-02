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
private:
   //--- Fallback: extract a string value by key using simple string search
   static string ExtractStringValue(string json, string key)
   {
      string searchKey = "\"" + key + "\"";
      int keyPos = StringFind(json, searchKey);
      if(keyPos == -1) return "";
      
      // Find the colon after the key
      int colonPos = StringFind(json, ":", keyPos + StringLen(searchKey));
      if(colonPos == -1) return "";
      
      // Find the opening quote of the value
      int quoteStart = StringFind(json, "\"", colonPos + 1);
      if(quoteStart == -1) return "";
      
      // Find the closing quote
      int quoteEnd = StringFind(json, "\"", quoteStart + 1);
      if(quoteEnd == -1) return "";
      
      return StringSubstr(json, quoteStart + 1, quoteEnd - quoteStart - 1);
   }
   
   //--- Fallback: extract an int value by key
   static int ExtractIntValue(string json, string key, int defaultValue)
   {
      string searchKey = "\"" + key + "\"";
      int keyPos = StringFind(json, searchKey);
      if(keyPos == -1) return defaultValue;
      
      int colonPos = StringFind(json, ":", keyPos + StringLen(searchKey));
      if(colonPos == -1) return defaultValue;
      
      // Skip whitespace after colon
      int valStart = colonPos + 1;
      while(valStart < StringLen(json))
      {
         string ch = StringSubstr(json, valStart, 1);
         if(ch != " " && ch != "\t" && ch != "\n" && ch != "\r")
            break;
         valStart++;
      }
      
      // Read digits
      int valEnd = valStart;
      while(valEnd < StringLen(json))
      {
         string ch = StringSubstr(json, valEnd, 1);
         if(ch < "0" || ch > "9")
            break;
         valEnd++;
      }
      
      if(valEnd == valStart) return defaultValue;
      string numStr = StringSubstr(json, valStart, valEnd - valStart);
      return (int)StringToInteger(numStr);
   }
   
   //--- Fallback: extract a bool value by key
   static bool ExtractBoolValue(string json, string key, bool defaultValue)
   {
      string searchKey = "\"" + key + "\"";
      int keyPos = StringFind(json, searchKey);
      if(keyPos == -1) return defaultValue;
      
      int colonPos = StringFind(json, ":", keyPos + StringLen(searchKey));
      if(colonPos == -1) return defaultValue;
      
      int valStart = colonPos + 1;
      while(valStart < StringLen(json))
      {
         string ch = StringSubstr(json, valStart, 1);
         if(ch != " " && ch != "\t" && ch != "\n" && ch != "\r")
            break;
         valStart++;
      }
      
      string val = StringSubstr(json, valStart, 4);
      if(val == "true") return true;
      val = StringSubstr(json, valStart, 5);
      if(val == "false") return false;
      return defaultValue;
   }

   //--- Read entire file as a string using binary read (avoids text-mode issues)
   static string ReadFileAsString(string filepath)
   {
      int handle = FileOpen(filepath, FILE_READ|FILE_BIN|FILE_COMMON);
      if(handle == INVALID_HANDLE)
         handle = FileOpen(filepath, FILE_READ|FILE_BIN);
      if(handle == INVALID_HANDLE)
         return "";
      
      ulong size = FileSize(handle);
      if(size == 0 || size > 65535)
      {
         FileClose(handle);
         return "";
      }
      
      uchar buffer[];
      ArrayResize(buffer, (int)size);
      FileReadArray(handle, buffer, 0, (int)size);
      FileClose(handle);
      
      return CharArrayToString(buffer, 0, WHOLE_ARRAY, CP_UTF8);
   }

public:
   //--- Load config from file, or return defaults if missing
   static ZmqConfiguration Load(string filename = "TradingBotZmqConfig.json")
   {
      ZmqConfiguration cfg;

      //--- Try binary read first (most reliable across platforms/encodings)
      string json = ReadFileAsString(filename);
      if(json == "")
      {
         // Fallback to text mode read
         int handle = FileOpen(filename, FILE_READ|FILE_TXT|FILE_COMMON);
         if(handle == INVALID_HANDLE)
            handle = FileOpen(filename, FILE_READ|FILE_TXT);

         if(handle == INVALID_HANDLE)
         {
            Print("[ConfigLoader] Config file not found: ", filename, ". Using defaults.");
            return cfg;
         }

         while(!FileIsEnding(handle))
            json += FileReadString(handle);
         FileClose(handle);
      }

      if(json == "")
      {
         Print("[ConfigLoader] Config file is empty. Using defaults.");
         return cfg;
      }

      return Parse(json, filename);
   }

   //--- Pure JSON/fallback extraction (testable without the Files sandbox)
   static ZmqConfiguration Parse(string json, string sourceName = "")
   {
      ZmqConfiguration cfg;

      // Log first part of JSON for debugging (truncate to avoid huge logs)
      string preview = json;
      if(StringLen(preview) > 200)
         preview = StringSubstr(preview, 0, 200) + "...";
      Print("[ConfigLoader] Raw JSON (", StringLen(json), " chars): ", preview);

      JSONValue *root = JSONParser::Parse(json);
      if(root == NULL || !root.IsObject())
      {
         Print("[ConfigLoader] JSONParser::Parse failed. Attempting string fallback...");
         if(root != NULL) delete root;

         //--- String fallback: extract critical fields manually
         string host = ExtractStringValue(json, "host");
         if(host != "")
         {
            cfg.host = host;
            cfg.marketPort     = ExtractIntValue(json, "marketPort", cfg.marketPort);
            cfg.commandPort    = ExtractIntValue(json, "commandPort", cfg.commandPort);
            cfg.queryPort      = ExtractIntValue(json, "queryPort", cfg.queryPort);
            cfg.heartbeatPort  = ExtractIntValue(json, "heartbeatPort", cfg.heartbeatPort);
            string pair = ExtractStringValue(json, "pair");
            if(pair != "") cfg.pair = pair;
            cfg.historyDays    = ExtractIntValue(json, "historyDays", cfg.historyDays);
            cfg.heartbeatSec   = ExtractIntValue(json, "heartbeatSec", cfg.heartbeatSec);
            cfg.magicNumber    = (ulong)ExtractIntValue(json, "magicNumber", (int)cfg.magicNumber);
            cfg.maxTicksPerSecond = ExtractIntValue(json, "maxTicksPerSecond", cfg.maxTicksPerSecond);
            cfg.batchSize      = ExtractIntValue(json, "batchSize", cfg.batchSize);
            cfg.autoConnectOnStartup = ExtractBoolValue(json, "autoConnectOnStartup", cfg.autoConnectOnStartup);
            cfg.autoShowPanel  = ExtractBoolValue(json, "autoShowPanel", cfg.autoShowPanel);
            cfg.simulateTrades = ExtractBoolValue(json, "simulateTrades", cfg.simulateTrades);
            string platformVersion = ExtractStringValue(json, "platformVersion");
            if(platformVersion != "") cfg.platformVersion = platformVersion;

            Print("[ConfigLoader] Config loaded via string fallback from ", sourceName);
            return cfg;
         }

         Print("[ConfigLoader] Failed to parse config JSON and string fallback also failed. Using defaults.");
         return cfg;
      }

      //--- Normal JSON path
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
      if(root.HasKey("simulateTrades")) cfg.simulateTrades = root["simulateTrades"].ToBool();
      if(root.HasKey("platformVersion")) cfg.platformVersion = root["platformVersion"].ToString();

      delete root;
      Print("[ConfigLoader] Config loaded from ", sourceName);
      return cfg;
   }
};
