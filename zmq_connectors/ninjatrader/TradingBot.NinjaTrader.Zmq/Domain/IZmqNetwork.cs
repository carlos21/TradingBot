using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;

namespace TradingBot.NinjaTrader.Zmq.Domain
{
    /// <summary>
    /// Facade pattern: Abstract ZMQ network operations.
    /// </summary>
    public interface IZmqNetwork : IDisposable
    {
        bool IsConnected { get; }
        void Start();
        void Stop();

        void SendTick(string pair, double price, long volume, DateTime time, double? bid = null, double? ask = null);
        void SendBar(string pair, DateTime time, double open, double high, double low, double close, long volume, bool isPartial = false, long seqNum = 0);
        void SendAuditResponse(string pair, List<JObject> bars);
        void SendHistoryBatch(string pair, List<JObject> bars, int days);
        void SendHistoryEnd();
        void SendRefreshStart();
        void SendEntryFill(string tradeId, double entryPrice, double? stopLoss = null, double? takeProfit = null, double? slippage = null, string account = null, double? quantity = null, double? accountBalance = null);
        void SendExitFill(string tradeId, double exitPrice, string resultType, string account = null, double? realizedPnl = null, double? commission = null, double? accountBalance = null);
        void SendTradeLog(string tradeId, string evt, string msg);
        void SendError(string source, string errorType, string message, string details = null);
        void SendHeartbeat(string source, string status);
        void SendConnect(string platform, string version, string account = null, string pair = null);
        void SendTestStart(string scenario, double entryPrice = 21000.0, double riskPoints = 80.0, double rrRatio = 1.0, JArray accounts = null);
        void SendTestResult(string scenario, bool passed, string tradeId = null, string message = "");
        void SendMarketStatus(bool marketOpen, DateTime nextOpen, string pair);
        void SendPositionSync(JArray positions, JArray untrackedOrders = null);
        void SendCommandAck(string commandType, int seqNum, bool success, string tradeId = null, string message = null);

        MessageEnvelope ReceiveCommand(int timeoutMs = 100);
        bool SendTestPingWithResponse(double timeoutMs = 2000);
        string QueryConfig(string key, double timeoutMs = 2000);
        JArray QueryPositions(double timeoutMs = 2000);
    }
}
