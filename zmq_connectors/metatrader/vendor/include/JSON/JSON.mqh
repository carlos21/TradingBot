//+------------------------------------------------------------------+
//|                                                      JSON/JSON.mqh|
//|  Minimal JSON library for MQL5 — matches the expected API         |
//|  Supports: JSONValue, JSONParser::Parse, operator[], Add, etc.    |
//+------------------------------------------------------------------+
#property strict

#ifndef JSON_MQH
#define JSON_MQH

//--- JSON value type constants
#define JSON_NULL    0
#define JSON_OBJECT  1
#define JSON_ARRAY   2
#define JSON_STRING  3
#define JSON_NUMBER  4
#define JSON_BOOL    5

//+------------------------------------------------------------------+
//| Forward declaration                                               |
//+------------------------------------------------------------------+
class JSONValue;

//+------------------------------------------------------------------+
//| JSONProxy — enables obj["key"] = value syntax                    |
//+------------------------------------------------------------------+
class JSONProxy
{
private:
   JSONValue *m_obj;
   string     m_key;

public:
   JSONProxy(JSONValue *obj, string key)
   {
      m_obj = obj;
      m_key = key;
   }

   void operator=(JSONValue *val);

   // Pass-through accessors for reading
   string ToString();
   long   ToInt();
   double ToDouble();
   bool   ToBool();
   bool   HasKey(string key);
   JSONProxy operator[](string key);
   bool   IsObject();
};

//+------------------------------------------------------------------+
//| JSONValue — universal JSON value container                        |
//+------------------------------------------------------------------+
class JSONValue
{
private:
   int          m_type;       // 0=null, 1=object, 2=array, 3=string, 4=number, 5=bool
   string       m_string;
   double       m_double;
   long         m_long;
   bool         m_bool;

   // Object/array storage
   string       m_keys[];
   JSONValue   *m_children[];

   bool KeyExists(string key, int &index)
   {
      int n = ArraySize(m_keys);
      for(int i = 0; i < n; i++)
      {
         if(m_keys[i] == key)
         {
            index = i;
            return true;
         }
      }
      return false;
   }

public:
   //--- Constructors
   JSONValue()
   {
      m_type = 0;
      m_string = "";
      m_double = 0;
      m_long = 0;
      m_bool = false;
   }

   JSONValue(int type)
   {
      m_type = type;
      m_string = "";
      m_double = 0;
      m_long = 0;
      m_bool = false;
   }

   JSONValue(string s)
   {
      m_type = 3;
      m_string = s;
      m_double = 0;
      m_long = 0;
      m_bool = false;
   }

   JSONValue(double d)
   {
      m_type = 4;
      m_double = d;
      m_long = (long)d;
      m_string = "";
      m_bool = false;
   }

   JSONValue(long l)
   {
      m_type = 4;
      m_long = l;
      m_double = (double)l;
      m_string = "";
      m_bool = false;
   }

   JSONValue(bool b)
   {
      m_type = 5;
      m_bool = b;
      m_string = "";
      m_double = 0;
      m_long = 0;
   }

   //--- Destructor recursively deletes children
   ~JSONValue()
   {
      int n = ArraySize(m_children);
      for(int i = 0; i < n; i++)
      {
         if(m_children[i] != NULL)
         {
            delete m_children[i];
            m_children[i] = NULL;
         }
      }
      ArrayResize(m_children, 0);
      ArrayResize(m_keys, 0);
   }

   //--- Type checks
   bool IsObject() { return m_type == 1; }
   bool IsArray()  { return m_type == 2; }
   bool IsNull()   { return m_type == 0; }

   //--- Key lookup
   bool HasKey(string key)
   {
      if(m_type != 1) return false;
      int idx;
      return KeyExists(key, idx);
   }

   //--- Read operator (returns pointer for direct use)
   JSONValue* Get(string key)
   {
      if(m_type != 1) return NULL;
      int idx;
      if(KeyExists(key, idx)) return m_children[idx];
      return NULL;
   }

   //--- operator[] returns proxy for both read and write
   JSONProxy operator[](string key)
   {
      return JSONProxy(GetPointer(this), key);
   }

   //--- Add to object
   void Add(string key, JSONValue *value)
   {
      if(m_type != 1) return;
      int idx;
      if(KeyExists(key, idx))
      {
         // Replace existing
         if(m_children[idx] != NULL) delete m_children[idx];
         m_children[idx] = value;
      }
      else
      {
         int n = ArraySize(m_keys);
         ArrayResize(m_keys, n + 1);
         ArrayResize(m_children, n + 1);
         m_keys[n] = key;
         m_children[n] = value;
      }
   }

   //--- Add to array
   void Add(JSONValue *value)
   {
      if(m_type != 2) return;
      int n = ArraySize(m_children);
      ArrayResize(m_children, n + 1);
      m_children[n] = value;
   }

   //--- Value accessors
   string ToString()
   {
      if(m_type == 3) return m_string;
      if(m_type == 4)
      {
         if(m_double == (double)m_long && MathAbs(m_double) < 9e18)
            return IntegerToString(m_long);
         return DoubleToString(m_double, 8);
      }
      if(m_type == 5) return m_bool ? "true" : "false";
      if(m_type == 0) return "null";
      return Serialize();
   }

   long ToInt()     { return m_long; }
   double ToDouble(){ return m_double; }
   bool ToBool()    { return m_bool; }

   //--- Serialization
   string Serialize()
   {
      if(m_type == 1)
      {
         string json = "{";
         int n = ArraySize(m_keys);
         for(int i = 0; i < n; i++)
         {
            if(i > 0) json += ",";
            json += "\"" + Escape(m_keys[i]) + "\":" + m_children[i].Serialize();
         }
         json += "}";
         return json;
      }
      if(m_type == 2)
      {
         string json = "[";
         int n = ArraySize(m_children);
         for(int i = 0; i < n; i++)
         {
            if(i > 0) json += ",";
            json += m_children[i].Serialize();
         }
         json += "]";
         return json;
      }
      if(m_type == 3) return "\"" + Escape(m_string) + "\"";  // String: must quote
      if(m_type == 5) return m_bool ? "true" : "false";        // Bool
      if(m_type == 0) return "null";                           // Null
      return ToString();                                        // Number
   }

   //--- Static helper
   static string Escape(string s)
   {
      string r = "";
      for(int i = 0; i < StringLen(s); i++)
      {
         ushort c = StringGetCharacter(s, i);
         switch(c)
         {
            case (ushort)34:  r += "\\\""; break;   // "
            case (ushort)92:  r += "\\\\"; break;   // \
            case (ushort)8:   r += "\\b";  break;   // backspace
            case (ushort)12:  r += "\\f";  break;   // form feed
            case (ushort)10:  r += "\\n";  break;   // newline
            case (ushort)13:  r += "\\r";  break;   // carriage return
            case (ushort)9:   r += "\\t";  break;   // tab
            default:
               if(c < 0x20)
                  r += StringFormat("\\u%04x", c);
               else
                  r += ShortToString(c);
         }
      }
      return r;
   }
};

//+------------------------------------------------------------------+
//| JSONProxy implementation                                          |
//+------------------------------------------------------------------+
void JSONProxy::operator=(JSONValue *val)
{
   if(m_obj != NULL)
      m_obj.Add(m_key, val);
}

string JSONProxy::ToString()
{
   JSONValue *v = m_obj.Get(m_key);
   return v != NULL ? v.ToString() : "";
}

long JSONProxy::ToInt()
{
   JSONValue *v = m_obj.Get(m_key);
   return v != NULL ? v.ToInt() : 0;
}

double JSONProxy::ToDouble()
{
   JSONValue *v = m_obj.Get(m_key);
   return v != NULL ? v.ToDouble() : 0.0;
}

bool JSONProxy::ToBool()
{
   JSONValue *v = m_obj.Get(m_key);
   return v != NULL ? v.ToBool() : false;
}

bool JSONProxy::HasKey(string key)
{
   JSONValue *v = m_obj.Get(m_key);
   return v != NULL ? v.HasKey(key) : false;
}

JSONProxy JSONProxy::operator[](string key)
{
   JSONValue *v = m_obj.Get(m_key);
   if(v != NULL) return JSONProxy(v, key);
   return JSONProxy(NULL, key);
}

bool JSONProxy::IsObject()
{
   JSONValue *v = m_obj.Get(m_key);
   return v != NULL ? v.IsObject() : false;
}

//+------------------------------------------------------------------+
//| JSONParser — static parse entry point                             |
//+------------------------------------------------------------------+
class JSONParser
{
public:
   static JSONValue* Parse(string json)
   {
      int pos = 0;
      JSONValue *result = NULL;
      pos = SkipSpace(json, pos);
      if(pos >= StringLen(json)) return NULL;
      pos = ParseValue(json, pos, result);
      return result;
   }

private:
   static int SkipSpace(string json, int pos)
   {
      int len = StringLen(json);
      while(pos < len)
      {
         ushort c = StringGetCharacter(json, pos);
         if(c == ' ' || c == '\t' || c == '\n' || c == '\r')
            pos++;
         else
            break;
      }
      return pos;
   }

   static int ParseValue(string json, int pos, JSONValue *&out)
   {
      pos = SkipSpace(json, pos);
      if(pos >= StringLen(json)) return pos;

      ushort c = StringGetCharacter(json, pos);

      if(c == '{')
         return ParseObject(json, pos, out);
      if(c == '[')
         return ParseArray(json, pos, out);
      if(c == '"')
      {
         string s;
         pos = ParseString(json, pos, s);
         out = new JSONValue(s);
         return pos;
      }
      if(c == 't' || c == 'f')
      {
         bool b;
         pos = ParseBool(json, pos, b);
         out = new JSONValue(b);
         return pos;
      }
      if(c == 'n')
      {
         pos = ParseNull(json, pos);
         out = new JSONValue();
         return pos;
      }
      // Number
      return ParseNumber(json, pos, out);
   }

   static int ParseObject(string json, int pos, JSONValue *&out)
   {
      out = new JSONValue(1); // JSON_OBJECT
      pos++; // skip '{'

      while(true)
      {
         pos = SkipSpace(json, pos);
         if(pos >= StringLen(json)) break;
         if(StringGetCharacter(json, pos) == '}')
         {
            pos++;
            break;
         }

         string key;
         pos = ParseString(json, pos, key);
         pos = SkipSpace(json, pos);
         if(pos < StringLen(json) && StringGetCharacter(json, pos) == ':')
            pos++;

         JSONValue *val = NULL;
         pos = ParseValue(json, pos, val);
         if(val != NULL)
            out.Add(key, val);

         pos = SkipSpace(json, pos);
         if(pos < StringLen(json) && StringGetCharacter(json, pos) == ',')
         {
            pos++;
            continue;
         }
         if(pos < StringLen(json) && StringGetCharacter(json, pos) == '}')
         {
            pos++;
            break;
         }
      }
      return pos;
   }

   static int ParseArray(string json, int pos, JSONValue *&out)
   {
      out = new JSONValue(2); // JSON_ARRAY
      pos++; // skip '['

      while(true)
      {
         pos = SkipSpace(json, pos);
         if(pos >= StringLen(json)) break;
         if(StringGetCharacter(json, pos) == ']')
         {
            pos++;
            break;
         }

         JSONValue *val = NULL;
         pos = ParseValue(json, pos, val);
         if(val != NULL)
            out.Add(val);

         pos = SkipSpace(json, pos);
         if(pos < StringLen(json) && StringGetCharacter(json, pos) == ',')
         {
            pos++;
            continue;
         }
         if(pos < StringLen(json) && StringGetCharacter(json, pos) == ']')
         {
            pos++;
            break;
         }
      }
      return pos;
   }

   static int ParseString(string json, int pos, string &out)
   {
      out = "";
      pos++; // skip opening quote
      int len = StringLen(json);
      while(pos < len)
      {
         ushort c = StringGetCharacter(json, pos);
         if(c == '"')
         {
            pos++;
            break;
         }
         if(c == '\\' && pos + 1 < len)
         {
            pos++;
            ushort next = StringGetCharacter(json, pos);
            switch(next)
            {
               case '"': out += "\""; break;
               case '\\': out += "\\"; break;
               case '/':  out += "/";  break;
               case 'b':  out += CharToString((uchar)8);  break;
               case 'f':  out += CharToString((uchar)12); break;
               case 'n':  out += "\n"; break;
               case 'r':  out += "\r"; break;
               case 't':  out += "\t"; break;
               case 'u':
                  if(pos + 4 < len)
                  {
                     string hex = StringSubstr(json, pos + 1, 4);
                     int code = (int)StringToInteger(hex);
                     out += ShortToString((ushort)code);
                     pos += 4;
                  }
                  break;
               default: out += ShortToString(next); break;
            }
         }
         else
         {
            out += ShortToString(c);
         }
         pos++;
      }
      return pos;
   }

   static int ParseNumber(string json, int pos, JSONValue *&out)
   {
      int start = pos;
      int len = StringLen(json);
      while(pos < len)
      {
         ushort c = StringGetCharacter(json, pos);
         if((c >= '0' && c <= '9') || c == '-' || c == '+' || c == '.' || c == 'e' || c == 'E')
            pos++;
         else
            break;
      }
      string numStr = StringSubstr(json, start, pos - start);
      if(StringFind(numStr, ".") >= 0 || StringFind(numStr, "e") >= 0 || StringFind(numStr, "E") >= 0)
      {
         out = new JSONValue(StringToDouble(numStr));
      }
      else
      {
         out = new JSONValue(StringToInteger(numStr));
      }
      return pos;
   }

   static int ParseBool(string json, int pos, bool &out)
   {
      if(StringSubstr(json, pos, 4) == "true")
      {
         out = true;
         pos += 4;
      }
      else if(StringSubstr(json, pos, 5) == "false")
      {
         out = false;
         pos += 5;
      }
      return pos;
   }

   static int ParseNull(string json, int pos)
   {
      if(StringSubstr(json, pos, 4) == "null")
         pos += 4;
      return pos;
   }
};
//+------------------------------------------------------------------+

#endif // JSON_MQH
