// ═══════════════════════════════════════════════════════════════════════
// Commands Layer: OrderCloseHandler
// Handles ORDER_CLOSE commands (Strategy Pattern)
// ═══════════════════════════════════════════════════════════════════════

using System;
using Newtonsoft.Json.Linq;
using NinjaTrader.Cbi;

namespace NinjaTrader.NinjaScript.AddOns
{
    /// <summary>
    /// Handles ORDER_CLOSE commands.
    /// </summary>
    internal sealed class OrderCloseHandler : ICommandHandler
    {
        public string CommandType => MessageType.OrderClose;

        private readonly ZmqNetwork _network;
        private readonly ILogger _logger;
        private readonly Account _account;
        private readonly string _instrument;
        private readonly IOrderTracker _orderTracker;

        public OrderCloseHandler(ZmqNetwork network, ILogger logger, Account account, 
            string instrument, IOrderTracker orderTracker)
        {
            _network = network ?? throw new ArgumentNullException(nameof(network));
            _logger = logger ?? throw new ArgumentNullException(nameof(logger));
            _account = account;
            _instrument = instrument;
            _orderTracker = orderTracker ?? throw new ArgumentNullException(nameof(orderTracker));
        }

        public void Handle(JObject payload)
        {
            try
            {
                var tradeId = payload?["trade_id"]?.ToString();
                if (string.IsNullOrEmpty(tradeId))
                    throw new ArgumentException("trade_id is required");

                if (_account == null)
                    throw new InvalidOperationException("No account available");

                _logger.Info($"CLOSE ORDER: {tradeId}");

                var instrument = Instrument.GetInstrument(_instrument);
                if (instrument == null)
                    throw new InvalidOperationException($"Instrument '{_instrument}' not found");

                // Get tracked orders for this trade
                var entryOrder = FindOrderByName($"Entry_{tradeId}");
                var stopOrder = FindOrderByName($"Stop_{tradeId}");
                var targetOrder = FindOrderByName($"Target_{tradeId}");

                // Cancel all working orders for this trade
                int cancelledCount = 0;
                
                if (entryOrder != null && IsWorking(entryOrder))
                {
                    _account.Cancel(new[] { entryOrder });
                    _logger.Info($"Cancelled entry order for {tradeId}");
                    cancelledCount++;
                }
                
                if (stopOrder != null && IsWorking(stopOrder))
                {
                    _account.Cancel(new[] { stopOrder });
                    _logger.Info($"Cancelled stop order for {tradeId}");
                    cancelledCount++;
                }
                
                if (targetOrder != null && IsWorking(targetOrder))
                {
                    _account.Cancel(new[] { targetOrder });
                    _logger.Info($"Cancelled target order for {tradeId}");
                    cancelledCount++;
                }

                // Check if we have a filled or partially filled position to close
                if (entryOrder != null && (entryOrder.OrderState == OrderState.Filled || entryOrder.OrderState == OrderState.PartFilled))
                {
                    // Submit closing market order for the FILLED quantity only
                    var closeQty = entryOrder.Filled;
                    if (closeQty <= 0) closeQty = entryOrder.Quantity;
                    var closeAction = entryOrder.OrderAction == OrderAction.Buy ? OrderAction.Sell : OrderAction.BuyToCover;
                    var closeOrder = _account.CreateOrder(
                        instrument,
                        closeAction,
                        OrderType.Market,
                        OrderEntry.Automated,
                        TimeInForce.Gtc,
                        closeQty,
                        0, 0, string.Empty, $"Close_{tradeId}", DateTime.MinValue, null);
                    
                    if (closeOrder != null)
                    {
                        // Track the close order so we know when it fills
                        _orderTracker.TrackCloseOrder(tradeId, closeOrder);
                        _logger.Success($"Closing market order submitted for {tradeId} ({closeAction} {closeQty} contracts)");
                    }
                    else
                    {
                        _logger.Error($"Failed to create closing order for {tradeId}");
                    }
                }
                else if (cancelledCount == 0)
                {
                    _logger.Warning($"No working orders or position found for {tradeId}");
                    // Remove from tracking since there's nothing to close
                    _orderTracker.RemoveTrade(tradeId);
                }
                else
                {
                    // Entry was cancelled but stop/target were working - just removed them
                    _logger.Info($"Cancelled {cancelledCount} working orders for {tradeId}");
                    _orderTracker.RemoveTrade(tradeId);
                }
                
                _network?.SendTradeLog(tradeId, "NT:CLOSE", $"Close command executed ({cancelledCount} orders cancelled)");
            }
            catch (Exception ex)
            {
                var tradeId = payload?["trade_id"]?.ToString() ?? "unknown";
                _logger.Error($"Order close failed for {tradeId}", ex);
                _network?.SendError("ninjatrader", "order_close_failed", $"Failed to close order {tradeId}: {ex.Message}");
            }
        }

        private Order FindOrderByName(string orderName)
        {
            if (_account == null || string.IsNullOrEmpty(orderName)) return null;
            
            foreach (var order in _account.Orders)
            {
                if (order.Name == orderName)
                    return order;
            }
            return null;
        }

        private static bool IsWorking(Order order)
        {
            return order.OrderState == OrderState.Working || 
                   order.OrderState == OrderState.Accepted ||
                   order.OrderState == OrderState.Submitted;
        }
    }
}
