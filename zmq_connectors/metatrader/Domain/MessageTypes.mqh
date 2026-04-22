//+------------------------------------------------------------------+
//|                                     Domain/MessageTypes.mqh      |
//|  Protocol constants — all msg_type strings shared with Python.   |
//+------------------------------------------------------------------+
#property strict

// Platform → Python (market data & events)
#define MT_TICK           "tick"
#define MT_BAR            "bar"
#define MT_PARTIAL        "partial"
#define MT_HISTORY_BATCH  "history_batch"
#define MT_HISTORY_END    "history_end"
#define MT_ENTRY_FILL     "entry_fill"
#define MT_EXIT_FILL      "exit_fill"
#define MT_TRADE_LOG      "trade_log"
#define MT_ERROR          "error"
#define MT_HEARTBEAT      "heartbeat"
#define MT_CONNECT        "connect"
#define MT_POSITION_SYNC  "position_sync"
#define MT_COMMAND_ACK    "command_ack"

// Python → Platform (commands)
#define MT_ORDER_OPEN     "order_open"
#define MT_ORDER_CLOSE    "order_close"
#define MT_ORDER_MODIFY   "order_modify"
#define MT_REFRESH_REQUEST "refresh_request"
#define MT_TEST_START     "test_start"

// Bidirectional queries (REQ/REP)
#define MT_TEST_PING      "test_ping"
#define MT_TEST_PONG      "test_pong"
#define MT_POSITION_QUERY "position_query"
#define MT_POSITION_RESPONSE "position_response"
#define MT_CONFIG_QUERY   "config_query"
#define MT_CONFIG_RESPONSE "config_response"
