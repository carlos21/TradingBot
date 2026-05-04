// ═══════════════════════════════════════════════════════════════════════
// Commands Layer: OrderModifyHandler
// Handles ORDER_MODIFY commands (Strategy Pattern)
// ═══════════════════════════════════════════════════════════════════════

using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json.Linq;
using NinjaTrader.Cbi;

namespace NinjaTrader.NinjaScript.AddOns
{
    /// <summary>
    /// Handles ORDER_MODIFY commands.
    /// Supports multi-account routing via "account" field in payload.
    /// </summary>
    internal sealed class OrderModifyHandler : ICommandHandler
    {
        public string CommandType => MessageType.OrderModify;

        private readonly ZmqNetwork _network;
        private readonly ILogger _logger;
        private readonly Dictionary<string, Account> _accounts;
        private readonly IOrderTracker _orderTracker;

        public OrderModifyHandler(ZmqNetwork network, ILogger logger, Dictionary<string, Account> accounts, IOrderTracker orderTracker)
        {
            _network = network ?? throw new ArgumentNullException(nameof(network));
            _logger = logger ?? throw new ArgumentNullException(nameof(logger));
            _accounts = accounts ?? throw new ArgumentNullException(nameof(accounts));
            _orderTracker = orderTracker ?? throw new ArgumentNullException(nameof(orderTracker));
        }

        public void Handle(JObject payload)
        {
            try
            {
                var tradeId = payload?["trade_id"]?.ToString();
                var newSl = payload?["stop_loss"]?.Value<double>() ?? 0;
                var accountName = payload?["account"]?.ToString();
                var account = ResolveAccount(accountName);

                if (string.IsNullOrEmpty(tradeId))
                    throw new ArgumentException("trade_id is required");
                if (newSl <= 0)
                    throw new ArgumentException($"Invalid stop_loss: {newSl}");
                if (account == null)
                    throw new InvalidOperationException($"No account available (requested: {accountName ?? "(default)"})");

                _logger.Info($"MODIFY ORDER: {tradeId} new SL={newSl} account={account.Name}");

                if (!_orderTracker.TryGetStopLoss(tradeId, out var stopOrder))
                {
                    // Try to find by scanning account orders (supports recovery after crash)
                    stopOrder = FindStopOrderForTrade(account, tradeId);
                    if (stopOrder == null)
                        throw new InvalidOperationException($"Stop order not found for trade {tradeId}");
                    
                    // Re-track the order for future lookups
                    _orderTracker.TrackStopLoss(tradeId, stopOrder);
                    _logger.Info($"[Recovery] Re-tracked stop order for {tradeId}");
                }

                if (stopOrder.OrderState != OrderState.Working && stopOrder.OrderState != OrderState.Accepted && stopOrder.OrderState != OrderState.Submitted)
                    throw new InvalidOperationException($"Stop order is not modifiable (state: {stopOrder.OrderState})");

                // Cancel + Replace pattern (event-driven)
                // Register pending modify so OnOrderUpdate can create the replacement
                // when the old order reports Cancelled state.
                _orderTracker.TrackPendingModify(tradeId, new PendingModifyInfo(
                    newSl, stopOrder.Instrument, stopOrder.OrderAction, stopOrder.Quantity));

                _orderTracker.ExpectCancellation(stopOrder.Name);
                account.Cancel(new[] { stopOrder });

                _logger.Info($"MODIFY PENDING: Cancelled stop for {tradeId}, replacement SL={newSl} queued");
                _network?.SendTradeLog(tradeId, "NT:MODIFY", $"Stop cancel requested, new SL={newSl} pending");
            }
            catch (Exception ex)
            {
                var tradeId = payload?["trade_id"]?.ToString() ?? "unknown";
                _logger.Warning($"SL modify failed for {tradeId}: {ex.Message}");
                _network?.SendError("ninjatrader", "order_modify_failed", $"Failed to modify order {tradeId}: {ex.Message}");
            }
        }

        private Account ResolveAccount(string accountName)
        {
            if (string.IsNullOrEmpty(accountName))
            {
                if (_accounts.Count == 1)
                {
                    foreach (var kvp in _accounts) return kvp.Value;
                }
                return null;
            }
            _accounts.TryGetValue(accountName, out var account);
            return account;
        }

        private Order FindStopOrderForTrade(Account account, string tradeId)
        {
            if (account == null) return null;
            
            // Look for order with trade_id embedded in name (e.g., "Stop_trade-123")
            string expectedName = $"Stop_{tradeId}";
            // Snapshot to avoid collection-modified-during-enumeration
            var orders = account.Orders.ToArray();
            foreach (var order in orders)
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
