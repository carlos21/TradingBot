//+------------------------------------------------------------------+
//| Module: Zmq/Zmq.mqh                                              |
//| Minimal ZMQ wrapper for MQL5 — direct DLL imports, no deps       |
//| Provides: Context, Socket, ZmqMsg, ZMQ constants                 |
//+------------------------------------------------------------------+
#property strict

//--- Socket types
#define ZMQ_PAIR        0
#define ZMQ_PUB         1
#define ZMQ_SUB         2
#define ZMQ_REQ         3
#define ZMQ_REP         4
#define ZMQ_DEALER      5
#define ZMQ_ROUTER      6
#define ZMQ_PULL        7
#define ZMQ_PUSH        8
#define ZMQ_XPUB        9
#define ZMQ_XSUB       10

//--- Flags
#define ZMQ_DONTWAIT    1
#define ZMQ_SNDMORE     2

//--- Errors
#define ZMQ_EAGAIN     11

//--- Socket options (subset used by this connector)
#define ZMQ_LINGER     17
#define ZMQ_RCVTIMEO   27

//--- DLL imports — use uchar arrays for string params to avoid UTF-16 issues
//   CRITICAL: len must be 'long' (64-bit) to match size_t on x64 Windows
#import "libzmq.dll"
   long zmq_ctx_new();
   int  zmq_ctx_term(long context);
   long zmq_socket(long context, int type);
   int  zmq_close(long socket);
   int  zmq_connect(long socket, uchar &addr[]);
   int  zmq_send(long socket, uchar &buf[], long len, int flags);
   int  zmq_recv(long socket, uchar &buf[], long len, int flags);
   int  zmq_setsockopt(long socket, int option, int &value, long len);
   int  zmq_errno();
#import

//+------------------------------------------------------------------+
//| ZmqMsg — simple string message wrapper                           |
//+------------------------------------------------------------------+
class ZmqMsg
{
private:
   string m_data;

public:
   ZmqMsg() { m_data = ""; }
   ZmqMsg(string data) { m_data = data; }
   ZmqMsg(const ZmqMsg &other) { m_data = other.m_data; }

   string getData() { return m_data; }
   void   setData(string data) { m_data = data; }
   int    size() { return StringLen(m_data); }
};

//+------------------------------------------------------------------+
//| Context — ZMQ context wrapper                                    |
//+------------------------------------------------------------------+
class Context
{
private:
   long m_ref;

public:
   Context() { m_ref = zmq_ctx_new(); Print("[Zmq] zmq_ctx_new() returned: ", m_ref); }
   Context(bool create) { m_ref = create ? zmq_ctx_new() : 0; }
   ~Context() { if(m_ref != 0) zmq_ctx_term(m_ref); }

   void operator=(Context *other)
   {
      if(other != NULL) m_ref = other.m_ref;
   }

   long ref() { return m_ref; }
};

//+------------------------------------------------------------------+
//| Socket — ZMQ socket wrapper                                      |
//+------------------------------------------------------------------+
class Socket
{
private:
   long m_ref;

public:
   Socket(Context &ctx, int type)
   {
      m_ref = zmq_socket(ctx.ref(), type);
      Print("[Zmq] zmq_socket(type=", type, ") returned: ", m_ref);
   }
   Socket(Context *ctx, int type)
   {
      m_ref = zmq_socket(ctx.ref(), type);
      Print("[Zmq] zmq_socket(type=", type, ") returned: ", m_ref);
   }
   ~Socket()
   {
      if(m_ref != 0) zmq_close(m_ref);
   }

   bool connect(string addr)
   {
      uchar buf[];
      int copied = StringToCharArray(addr, buf, 0, WHOLE_ARRAY, CP_UTF8);
      if(copied <= 0) return false;
      // StringToCharArray includes null terminator in copied count
      int len = copied - 1;

      int rc = zmq_connect(m_ref, buf);
      int err = zmq_errno();
      Print("[Zmq] zmq_connect('", addr, "') rc=", rc, " errno=", err);
      return rc == 0;
   }

   bool send(ZmqMsg &msg)
   {
      string data = msg.getData();
      uchar buf[];
      int copied = StringToCharArray(data, buf, 0, WHOLE_ARRAY, CP_UTF8);
      if(copied <= 0) return false;
      // StringToCharArray includes null terminator in copied count
      int len = copied - 1;

      int result = zmq_send(m_ref, buf, (long)len, 0);
      return result >= 0;
   }

   bool send(ZmqMsg &msg, bool more)
   {
      string data = msg.getData();
      uchar buf[];
      int copied = StringToCharArray(data, buf, 0, WHOLE_ARRAY, CP_UTF8);
      if(copied <= 0) return false;
      // StringToCharArray includes null terminator in copied count
      int len = copied - 1;

      int flags = more ? ZMQ_SNDMORE : 0;
      int result = zmq_send(m_ref, buf, (long)len, flags);
      return result >= 0;
   }

   bool recv(ZmqMsg &msg, int flags = 0)
   {
      uchar buf[8192];
      int len = zmq_recv(m_ref, buf, (long)8192, flags);
      if(len > 0)
      {
         msg.setData(CharArrayToString(buf, 0, len, CP_UTF8));
         return true;
      }
      // Log unexpected errors (not just EAGAIN which is normal for non-blocking)
      int err = zmq_errno();
      if(err != ZMQ_EAGAIN)
      {
         Print("[Zmq] recv error: len=" + IntegerToString(len) + " errno=" + IntegerToString(err));
      }
      return false;
   }

   //--- Socket options (int-valued; blocks zmq_recv at most `ms` milliseconds)
   bool setReceiveTimeout(int ms)
   {
      int value = ms;
      return 0 == zmq_setsockopt(m_ref, ZMQ_RCVTIMEO, value, 4);
   }

   bool setLinger(int ms)
   {
      int value = ms;
      return 0 == zmq_setsockopt(m_ref, ZMQ_LINGER, value, 4);
   }
};
//+------------------------------------------------------------------+
