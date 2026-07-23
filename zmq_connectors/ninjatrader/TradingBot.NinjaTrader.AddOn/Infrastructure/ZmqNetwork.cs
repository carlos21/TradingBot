using System;
using System.Collections.Generic;
using System.Threading;
using NetMQ;
using NetMQ.Sockets;
using Newtonsoft.Json.Linq;
using NinjaTrader.Core;
using TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.AddOn.Infrastructure
{
    /// <summary>
    /// Network layer: Manages all ZeroMQ socket operations.
    /// </summary>
    public sealed class ZmqNetwork : IZmqNetwork
    {
        private PublisherSocket _marketPub;
        private PullSocket _commandPull;
        private RequestSocket _queryReq;
        private PublisherSocket _heartbeatPub;

        private readonly ZmqConfiguration _config;
        private readonly IMessageSerializer _serializer;
        private readonly ILogger _logger;

        private int _seqNum = 0;
        private readonly object _seqLock = new object();
        private readonly object _sendLock = new object();
        private readonly object _recvLock = new object();
        private readonly object _queryLock = new object();
        private readonly object _heartbeatLock = new object();

        public bool IsConnected => _marketPub != null && _commandPull != null;

        public ZmqNetwork(ZmqConfiguration config, IMessageSerializer serializer, ILogger logger)
        {
            _config = config ?? throw new ArgumentNullException(nameof(config));
            _serializer = serializer ?? throw new ArgumentNullException(nameof(serializer));
            _logger = logger ?? throw new ArgumentNullException(nameof(logger));
        }

        public void Start()
        {
            _marketPub = new PublisherSocket();
            _marketPub.Options.SendHighWatermark = 10000;
            EnableTcpKeepalive(_marketPub);
            _marketPub.Connect(_config.MarketDataAddress);

            _commandPull = new PullSocket();
            EnableTcpKeepalive(_commandPull);
            _commandPull.Connect(_config.CommandAddress);

            _queryReq = new RequestSocket();
            EnableTcpKeepalive(_queryReq);
            _queryReq.Connect(_config.QueryAddress);

            _heartbeatPub = new PublisherSocket();
            _heartbeatPub.Options.SendHighWatermark = 1000;
            EnableTcpKeepalive(_heartbeatPub);
            _heartbeatPub.Connect(_config.HeartbeatAddress);

            _logger?.Info($"Connected to ZMQ endpoints: market={_config.MarketPort}, cmd={_config.CommandPort}");
        }

        // Lets passive sockets (especially the command PullSocket) detect a half-open
        // TCP connection (e.g. Python backend / WSL VM restarted) and reconnect on
        // their own, instead of sitting wedged until NinjaTrader is restarted.
        private static void EnableTcpKeepalive(NetMQSocket socket)
        {
            socket.Options.TcpKeepalive = true;
            socket.Options.TcpKeepaliveIdle = TimeSpan.FromSeconds(10);
            socket.Options.TcpKeepaliveInterval = TimeSpan.FromSeconds(5);
            socket.Options.TcpKeepaliveCnt = 3;
        }

        public void Stop()
        {
            _logger?.Info("ZMQ network stopping...");

            lock (_sendLock)
            lock (_recvLock)
            lock (_queryLock)
            lock (_heartbeatLock)
            {
                SafeDispose(ref _marketPub);
                SafeDispose(ref _commandPull);
                SafeDispose(ref _queryReq);
                SafeDispose(ref _heartbeatPub);
            }

            _logger?.Info("ZMQ network stopped");
        }

        private static void SafeDispose<T>(ref T socket) where T : class, IDisposable
        {
            if (socket == null) return;
            try { socket.Dispose(); }
            catch { /* Best-effort dispose */ }
            socket = null;
        }

        public void Dispose() => Stop();

        private int NextSeq()
        {
            lock (_seqLock) { return ++_seqNum; }
        }

        private void Send(string msgType, JObject payload)
        {
            var envelope = MessageEnvelope.Create(msgType, payload, NextSeq());
            lock (_sendLock)
            {
                if (_marketPub == null) return;
                _marketPub?.SendFrame(_serializer.Serialize(envelope));
            }
        }

        public void SendTick(string pair, double price, long volume, DateTime time, double? bid = null, double? ask = null)
        {
            var payload = new JObject
            {
                ["pair"] = pair,
                ["price"] = price,
                ["volume"] = volume,
                ["time"] = ToUnixSeconds(time)
            };
            if (bid.HasValue) payload["bid"] = bid.Value;
            if (ask.HasValue) payload["ask"] = ask.Value;
            Send(MessageType.Tick, payload);
        }

        private int _completedBarsSent = 0;
        private int _partialBarsSent = 0;

        public void SendBar(string pair, DateTime time, double open, double high, double low, double close, long volume, bool isPartial = false, long seqNum = 0)
        {
            var payload = new JObject
            {
                ["pair"] = pair,
                ["time"] = ToUnixSeconds(time),
                ["open"] = open,
                ["high"] = high,
                ["low"] = low,
                ["close"] = close,
                ["volume"] = volume
            };
            if (seqNum > 0)
                payload["seq_num"] = seqNum;
            if (isPartial)
            {
                int count = Interlocked.Increment(ref _partialBarsSent);
                if (count % 50 == 0)
                    _logger.Info($"[ZMQ] Sent {count} partial bars (latest {pair} @{close:F2})");
                Send(MessageType.PartialBar, payload);
            }
            else
            {
                int count = Interlocked.Increment(ref _completedBarsSent);
                if (count % 50 == 0)
                    _logger.Info($"[ZMQ] Sent {count} completed bars (latest {pair} @{close:F2} time={ToUnixSeconds(time)})");
                Send(MessageType.Bar, payload);
            }
        }

        public void SendAuditResponse(string pair, List<JObject> bars)
        {
            Send(MessageType.AuditResponse, new JObject
            {
                ["pair"] = pair,
                ["bars"] = new JArray(bars),
                ["count"] = bars.Count
            });
        }

        public void SendHistoryBatch(string pair, List<JObject> bars, int days)
        {
            Send(MessageType.HistoryBatch, new JObject
            {
                ["pair"] = pair,
                ["bars"] = new JArray(bars),
                ["days"] = days
            });
        }

        public void SendHistoryEnd(string pair) => Send(MessageType.HistoryEnd, new JObject { ["pair"] = pair });
        public void SendRefreshStart(string pair) => Send(MessageType.RefreshStart, new JObject { ["pair"] = pair });

        public void SendEntryFill(string tradeId, double entryPrice, double? stopLoss = null, double? takeProfit = null, double? slippage = null, string account = null, double? quantity = null, double? accountBalance = null)
        {
            var payload = new JObject { ["trade_id"] = tradeId, ["entry_price"] = entryPrice };
            if (stopLoss.HasValue) payload["stop_loss"] = stopLoss.Value;
            if (takeProfit.HasValue) payload["take_profit"] = takeProfit.Value;
            if (slippage.HasValue) payload["slippage"] = slippage.Value;
            if (account != null) payload["account"] = account;
            if (quantity.HasValue) payload["quantity"] = quantity.Value;
            if (accountBalance.HasValue) payload["account_balance"] = accountBalance.Value;
            Send(MessageType.EntryFill, payload);
        }

        public void SendExitFill(string tradeId, double exitPrice, string resultType, string account = null, double? realizedPnl = null, double? commission = null, double? accountBalance = null)
        {
            var payload = new JObject
            {
                ["trade_id"] = tradeId,
                ["exit_price"] = exitPrice,
                ["result_type"] = resultType,
                ["exit_time"] = ToUnixSeconds(DateTime.UtcNow)
            };
            if (account != null) payload["account"] = account;
            if (realizedPnl.HasValue) payload["realized_pnl"] = realizedPnl.Value;
            if (commission.HasValue) payload["commission"] = commission.Value;
            if (accountBalance.HasValue) payload["account_balance"] = accountBalance.Value;
            Send(MessageType.ExitFill, payload);
        }

        public void SendTradeLog(string tradeId, string evt, string msg)
        {
            Send(MessageType.TradeLog, new JObject
            {
                ["trade_id"] = tradeId,
                ["event"] = evt,
                ["message"] = msg?.Replace("\"", "'") ?? ""
            });
        }

        public void SendError(string source, string errorType, string message, string details = null)
        {
            var payload = new JObject
            {
                ["source"] = source,
                ["error_type"] = errorType,
                ["message"] = message?.Replace("\"", "'"),
                ["timestamp"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0
            };
            if (details != null)
                payload["details"] = details.Replace("\"", "'").Replace("\r\n", " | ").Replace("\n", " | ");
            Send(MessageType.Error, payload);
        }

        public void SendHeartbeat(string source, string status)
        {
            if (_heartbeatPub == null) return;
            var payload = new JObject { ["source"] = source, ["status"] = status };
            var envelope = MessageEnvelope.Create(MessageType.Heartbeat, payload, NextSeq());
            lock (_heartbeatLock)
            {
                _heartbeatPub?.SendFrame(_serializer.Serialize(envelope));
            }
        }

        public void SendConnect(string platform, string version, string account = null, string pair = null)
        {
            var payload = new JObject { ["platform"] = platform, ["version"] = version };
            if (account != null) payload["account"] = account;
            if (pair != null) payload["pair"] = pair;
            Send(MessageType.Connect, payload);
        }

        public void SendTestStart(string scenario, double entryPrice = 21000.0, double riskPoints = 80.0, double rrRatio = 1.0, JArray accounts = null)
        {
            var payload = new JObject
            {
                ["scenario"] = scenario,
                ["entry_price"] = entryPrice,
                ["risk_points"] = riskPoints,
                ["rr_ratio"] = rrRatio
            };
            if (accounts != null)
                payload["accounts"] = accounts;
            Send(MessageType.TestStart, payload);
        }

        public void SendTestResult(string scenario, bool passed, string tradeId = null, string message = "")
        {
            var payload = new JObject { ["scenario"] = scenario, ["passed"] = passed, ["message"] = message };
            if (tradeId != null) payload["trade_id"] = tradeId;
            Send(MessageType.TestResult, payload);
        }

        public void SendMarketStatus(bool marketOpen, DateTime nextOpen, string pair)
        {
            var payload = new JObject
            {
                ["market_open"] = marketOpen,
                ["next_open"] = ToUnixSeconds(nextOpen),
                ["pair"] = pair
            };
            Send(MessageType.MarketStatus, payload);
        }

        public void SendPositionSync(JArray positions, JArray untrackedOrders = null)
        {
            var payload = new JObject
            {
                ["positions"] = positions,
                ["count"] = positions.Count,
                ["source"] = "ninjatrader",
                ["is_source_of_truth"] = true,
            };

            if (untrackedOrders != null && untrackedOrders.Count > 0)
                payload["untracked_orders"] = untrackedOrders;

            Send(MessageType.PositionSync, payload);
        }

        public void SendCommandAck(string commandType, int seqNum, bool success, string tradeId = null, string message = null)
        {
            var payload = new JObject
            {
                ["command_type"] = commandType,
                ["seq_num"] = seqNum,
                ["success"] = success,
                ["timestamp"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0,
            };

            if (tradeId != null)
                payload["trade_id"] = tradeId;
            if (message != null)
                payload["message"] = message;

            Send(MessageType.CommandAck, payload);
        }

        public MessageEnvelope ReceiveCommand(int timeoutMs = 100)
        {
            if (_commandPull == null) return null;
            lock (_recvLock)
            {
                if (_commandPull == null) return null;
                if (_commandPull.TryReceiveFrameString(TimeSpan.FromMilliseconds(timeoutMs), out string message))
                    return _serializer.Deserialize(message);
                return null;
            }
        }

        public bool SendTestPingWithResponse(double timeoutMs = 2000)
        {
            if (_queryReq == null) return false;
            try
            {
                var payload = new JObject { ["timestamp"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0 };
                var envelope = MessageEnvelope.Create(MessageType.TestPing, payload, NextSeq());
                string response;
                lock (_queryLock)
                {
                    _queryReq.SendFrame(_serializer.Serialize(envelope));
                    if (!_queryReq.TryReceiveFrameString(TimeSpan.FromMilliseconds(timeoutMs), out response))
                    {
                        _logger?.Warning("Test ping timeout — recreating REQ socket");
                        RecreateQueryReq();
                        return false;
                    }
                }
                var resp = _serializer.Deserialize(response);
                return resp?.MsgType == MessageType.TestPong;
            }
            catch (Exception ex)
            {
                _logger?.Warning($"Test ping failed: {ex.Message}");
                return false;
            }
        }

        public string QueryConfig(string key, double timeoutMs = 2000)
        {
            if (_queryReq == null) return null;
            try
            {
                var payload = new JObject { ["key"] = key };
                var envelope = MessageEnvelope.Create(MessageType.ConfigQuery, payload, NextSeq());
                string response;
                lock (_queryLock)
                {
                    _queryReq.SendFrame(_serializer.Serialize(envelope));
                    if (!_queryReq.TryReceiveFrameString(TimeSpan.FromMilliseconds(timeoutMs), out response))
                    {
                        _logger?.Warning("QueryConfig timeout — recreating REQ socket");
                        RecreateQueryReq();
                        return null;
                    }
                }
                var resp = _serializer.Deserialize(response);
                if (resp?.MsgType == MessageType.ConfigResponse)
                    return resp.Payload[key]?.ToString();
                return null;
            }
            catch (Exception ex)
            {
                _logger?.Warning($"QueryConfig failed: {ex.Message}");
                return null;
            }
        }

        public JArray QueryPositions(double timeoutMs = 2000)
        {
            if (_queryReq == null) return null;
            try
            {
                var payload = new JObject();
                var envelope = MessageEnvelope.Create(MessageType.PositionQuery, payload, NextSeq());
                string response;
                lock (_queryLock)
                {
                    _queryReq.SendFrame(_serializer.Serialize(envelope));
                    if (!_queryReq.TryReceiveFrameString(TimeSpan.FromMilliseconds(timeoutMs), out response))
                    {
                        _logger?.Warning("QueryPositions timeout — recreating REQ socket");
                        RecreateQueryReq();
                        return null;
                    }
                }
                var resp = _serializer.Deserialize(response);
                if (resp?.MsgType == MessageType.PositionResponse)
                    return resp.Payload["positions"] as JArray;
                return null;
            }
            catch (Exception ex)
            {
                _logger?.Warning($"QueryPositions failed: {ex.Message}");
                return null;
            }
        }

        private void RecreateQueryReq()
        {
            lock (_queryLock)
            {
                SafeDispose(ref _queryReq);
                _queryReq = new RequestSocket();
                _queryReq.Connect(_config.QueryAddress);
                _logger?.Info("REQ socket recreated");
            }
        }

        private static double ToUnixSeconds(DateTime dt)
        {
            DateTime utc;
            if (dt.Kind == DateTimeKind.Utc)
            {
                utc = dt;
            }
            else
            {
                // NinjaTrader bar/tick times are in platform time; convert to UTC.
                var platformTime = DateTime.SpecifyKind(dt, DateTimeKind.Unspecified);
                utc = TimeZoneInfo.ConvertTimeToUtc(platformTime, Globals.GeneralOptions.TimeZoneInfo);
            }
            return (utc - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalSeconds;
        }
    }
}
