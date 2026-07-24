using System;
using System.Threading;
using Newtonsoft.Json.Linq;
using TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.Zmq.Application.Handlers
{
    /// <summary>
    /// Handles a deliberate disconnect requested by the Python side (e.g. the user
    /// stopped the stream from the UI). Like SubscribeHandler, success is reported
    /// by returning true — the command loop then sends the success ACK.
    /// </summary>
    public sealed class DisconnectHandler : ICommandHandler
    {
        public string CommandType => MessageType.Disconnect;

        private readonly IZmqNetwork _network;
        private readonly ILogger _logger;
        private readonly IConnectorService _connectorService;

        public DisconnectHandler(IZmqNetwork network, ILogger logger, IConnectorService connectorService)
        {
            _network = network ?? throw new ArgumentNullException(nameof(network));
            _logger = logger ?? throw new ArgumentNullException(nameof(logger));
            _connectorService = connectorService ?? throw new ArgumentNullException(nameof(connectorService));
        }

        public bool Handle(JObject payload)
        {
            try
            {
                var reason = payload?["reason"]?.Value<string>();
                _logger.Info($"DISCONNECT command: {reason ?? "<no reason>"}");

                // Disconnect on a separate thread: Handle runs ON the command thread,
                // and Disconnect() joins that thread (self-join would stall it) and
                // stops the network before the command loop can flush the success ACK.
                // The short delay lets the ACK go out first.
                var disconnectThread = new Thread(() =>
                {
                    try
                    {
                        Thread.Sleep(100);
                        _connectorService.Disconnect("Python requested disconnect");
                    }
                    catch (Exception ex)
                    {
                        _logger.Error("Deferred disconnect failed", ex);
                    }
                })
                { IsBackground = true, Name = "ZMQ-Disconnect" };
                disconnectThread.Start();

                return true;
            }
            catch (Exception ex)
            {
                _logger.Error("Disconnect command failed", ex);
                _network.SendError("ninjatrader", "disconnect_failed", ex.Message);
                return false;
            }
        }
    }
}
