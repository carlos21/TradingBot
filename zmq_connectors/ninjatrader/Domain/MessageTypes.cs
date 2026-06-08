// ═══════════════════════════════════════════════════════════════════════
// Domain Layer: Message Types
// Protocol message type constants
// ═══════════════════════════════════════════════════════════════════════

namespace NinjaTrader.NinjaScript.AddOns
{
    public static class MessageType
    {
        // Market Data
        public const string Tick = "tick";
        public const string Bar = "bar";
        public const string PartialBar = "partial";
        public const string HistoryBatch = "history_batch";
        public const string HistoryEnd = "history_end";

        // Order Events
        public const string OrderOpen = "order_open";
        public const string OrderClose = "order_close";
        public const string OrderModify = "order_modify";
        public const string EntryFill = "entry_fill";
        public const string ExitFill = "exit_fill";
        public const string OrderRejected = "order_rejected";
        public const string TradeLog = "trade_log";
        public const string CommandAck = "command_ack";

        // System
        public const string Error = "error";
        public const string Heartbeat = "heartbeat";
        public const string Connect = "connect";
        public const string Disconnect = "disconnect";
        public const string RefreshRequest = "refresh_request";
        public const string RefreshStart = "refresh_start";
        public const string PositionQuery = "position_query";
        public const string PositionResponse = "position_response";
        public const string PositionSync = "position_sync";
        public const string ConfigQuery = "config_query";
        public const string ConfigResponse = "config_response";
        public const string MarketStatus = "market_status";

        // Testing
        public const string TestPing = "test_ping";
        public const string TestPong = "test_pong";
        public const string TestStart = "test_start";
        public const string TestStatus = "test_status";
        public const string TestResult = "test_result";
    }
}
