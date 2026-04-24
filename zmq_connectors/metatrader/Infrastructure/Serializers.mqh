//+------------------------------------------------------------------+
//|                                Infrastructure/Serializers.mqh    |
//|  IMessageSerializer using MQL5 native JSON API.                  |
//|  Pattern: Strategy (JSON serialization strategy)                 |
//+------------------------------------------------------------------+
#property strict

#include "../Domain/Contracts.mqh"
#include <JSON/JSON.mqh>

//+------------------------------------------------------------------+
//| JsonMessageSerializer — MQL5 JSONValue wrapper                   |
//+------------------------------------------------------------------+
class JsonMessageSerializer : public IMessageSerializer
{
private:
   ILogger *m_logger;

public:
   JsonMessageSerializer(ILogger *logger)
   {
      m_logger = logger;
   }

   ~JsonMessageSerializer() {}

   //--- IMessageSerializer implementation
   string Serialize(JSONValue *root) override
   {
      if(root == NULL)
      {
         if(m_logger != NULL)
            m_logger.Warning("Serialize called with NULL root");
         return "{}";
      }
      return root.ToString();
   }

   JSONValue *Deserialize(string json) override
   {
      if(StringLen(json) == 0)
         return NULL;

      JSONValue *root = JSONParser::Parse(json);
      if(root == NULL && m_logger != NULL)
      {
         m_logger.Warning("JSON parse failed: " + json);
      }
      return root;
   }
};
