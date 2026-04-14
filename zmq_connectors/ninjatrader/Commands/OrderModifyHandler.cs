// ═══════════════════════════════════════════════════════════════════════
// Commands Layer: OrderModifyHandler
// Handles ORDER_MODIFY commands (Strategy Pattern)
// ═══════════════════════════════════════════════════════════════════════

using System;
using System.Threading;
using Newtonsoft.Json.Linq;
using NinjaTrader.Cbi;

namespace NinjaTrader.NinjaScript.AddOns
{
    /// <summary>
    /// Handles ORDER_MODIFY commands.
    /// </summary>
    internal sealed class OrderModifyHandler : ICommandHandler
    {
        public string CommandType => MessageType.OrderModify;

        private readonly ZmqNetwork _network;
        private readonly ILogger _logger;
        private readonly Account _account;
        private readonly IOrderTracker _orderTracker;

        public OrderModifyHandler(ZmqNetwork network, ILogger logger, Account account, IOrderTracker orderTracker)
        {
            _network = network ?? throw new ArgumentNullException(nameof(network));
            _logger = logger ?? throw new ArgumentNullException(nameof(logger));
            _account = account;
            _orderTracker = orderTracker ?? throw new ArgumentNullException(nameof(orderTracker));
        }

        public void Handle(JObject payload)
        {
            try
            {
                var tradeId = payload?["trade_id"]?.ToString();
                var newSl = payload?["stop_loss"]?.Value<double>() ?? 0;

                if (string.IsNullOrEmpty(tradeId))
                    throw new ArgumentException("trade_id is required");
                if (newSl <= 0)
                    throw new ArgumentException($"Invalid stop_loss: {newSl}");
                if (_account == null)
                    throw new InvalidOperationException("No account available");

                _logger.Info($"MODIFY ORDER: {tradeId} new SL={newSl}");

                if (!_orderTracker.TryGetStopLoss(tradeId, out var stopOrder))
                {
                    // Try to find by scanning account orders (supports recovery after crash)
                    stopOrder = FindStopOrderForTrade(tradeId);
                    if (stopOrder == null)
                        throw new InvalidOperationException($"Stop order not found for trade {tradeId}");
                    
                    // Re-track the order for future lookups
                    _orderTracker.TrackStopLoss(tradeId, stopOrder);
                    _logger.Info($"[Recovery] Re-tracked stop order for {tradeId}");
                }

                if (stopOrder.OrderState != OrderState.Working && stopOrder.OrderState != OrderState.Accepted && stopOrder.OrderState != OrderState.Submitted)
                    throw new InvalidOperationException($"Stop order is not modifiable (state: {stopOrder.OrderState})");

                // Cancel + Replace pattern
                _account.Cancel(new[] { stopOrder });
                Thread.Sleep(50);

                var newStopOrder = _account.CreateOrder(
                    stopOrder.Instrument,
                    stopOrder.OrderAction,
                    OrderType.StopMarket,
                    OrderEntry.Automated,
                    TimeInForce.Gtc,
                    stopOrder.Quantity,
                    0,
                    newSl,
                    string.Empty,
                    $"Stop_{tradeId}",  // CRITICAL: Must include trade_id for recovery
                    DateTime.MinValue,
                    null);

                if (newStopOrder == null)
                    throw new InvalidOperationException("Failed to create new stop order");

                _orderTracker.TrackStopLoss(tradeId, newStopOrder);

                _logger.Success($"Modified SL for {tradeId} from {stopOrder.StopPrice} to {newSl}");
                _network?.SendTradeLog(tradeId, "NT:MODIFY", $"Stop loss changed from {stopOrder.StopPrice} to {newSl}");
            }
            catch (Exception ex)
            {
                var tradeId = payload?["trade_id"]?.ToString() ?? "unknown";
                _logger.Warning($"SL modify failed for {tradeId}: {ex.Message}");
                _network?.SendError("ninjatrader", "order_modify_failed", $"Failed to modify order {tradeId}: {ex.Message}");
            }
        }

        private Order FindStopOrderForTrade(string tradeId)
        {
            if (_account == null) return null;
            
            // Look for order with trade_id embedded in name (e.g., "Stop_trade-123")
            string expectedName = $"Stop_{tradeId}";
            foreach (var order in _account.Orders)
            {
                if (order.Name == expectedName &&
                    (order.OrderState == OrderState.Working || order.OrderState == OrderState.Accepted || order.OrderState == OrderState.Submitted))
                {
                    return order;
                }
            }
            
            return null;
        }
    }
}
