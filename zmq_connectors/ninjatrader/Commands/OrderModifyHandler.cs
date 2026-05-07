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
        private readonly IOrderTracker _orderTracker;
        private readonly bool _simulate;

        public OrderModifyHandler(ZmqNetwork network, ILogger logger, IOrderTracker orderTracker, bool simulate = false)
        {
            _network = network ?? throw new ArgumentNullException(nameof(network));
            _logger = logger ?? throw new ArgumentNullException(nameof(logger));
            _orderTracker = orderTracker ?? throw new ArgumentNullException(nameof(orderTracker));
            _simulate = simulate;
        }

        public bool Handle(JObject payload)
        {
            try
            {
                var tradeId = payload?["trade_id"]?.ToString();
                var newSl = payload?["stop_loss"]?.Value<double>() ?? 0;
                var newTp = payload?["take_profit"]?.Value<double>() ?? 0;
                var accountName = payload?["account"]?.ToString();

                if (string.IsNullOrEmpty(tradeId))
                    throw new ArgumentException("trade_id is required");
                if (newSl <= 0)
                    throw new ArgumentException($"Invalid stop_loss: {newSl}");

                // ── SIMULATE MODE: Log and return success, NO account/broker lookup ──
                if (_simulate || TradingBotZmqConnector.E2ETestRunning)
                {
                    _logger.Info($"🧪 SIMULATE MODIFY: {tradeId} new SL={newSl} new TP={newTp} account={accountName ?? "default"}");
                    _network?.SendTradeLog(tradeId, "NT:SIMULATE", $"Simulated modify SL={newSl} TP={newTp}");
                    return true;
                }

                var account = ResolveAccount(accountName);
                if (account == null)
                    throw new InvalidOperationException($"No account available (requested: {accountName ?? "(default)"})");

                _logger.Info($"MODIFY ORDER: {tradeId} new SL={newSl} new TP={newTp} account={account.Name}");

                bool modifiedAny = false;

                // --- Modify Stop Loss ---
                if (newSl > 0)
                {
                    if (!_orderTracker.TryGetStopLoss(tradeId, out var stopOrder))
                    {
                        stopOrder = FindStopOrderForTrade(account, tradeId);
                        if (stopOrder == null)
                            throw new InvalidOperationException($"Stop order not found for trade {tradeId}");
                        _orderTracker.TrackStopLoss(tradeId, stopOrder);
                        _logger.Info($"[Recovery] Re-tracked stop order for {tradeId}");
                    }

                    if (stopOrder.OrderState != OrderState.Working && stopOrder.OrderState != OrderState.Accepted && stopOrder.OrderState != OrderState.Submitted && stopOrder.OrderState != OrderState.PartFilled)
                        throw new InvalidOperationException($"Stop order is not modifiable (state: {stopOrder.OrderState})");

                    _orderTracker.TrackPendingModify(tradeId, new PendingModifyInfo(
                        newSl, stopOrder.Instrument, stopOrder.OrderAction, stopOrder.Quantity));
                    _orderTracker.ExpectCancellation(stopOrder.Name);
                    account.Cancel(new[] { stopOrder });
                    modifiedAny = true;
                }

                // --- Modify Take Profit ---
                if (newTp > 0)
                {
                    if (!_orderTracker.TryGetTakeProfit(tradeId, out var targetOrder))
                    {
                        targetOrder = FindTargetOrderForTrade(account, tradeId);
                        if (targetOrder == null)
                            throw new InvalidOperationException($"Target order not found for trade {tradeId}");
                        _orderTracker.TrackTakeProfit(tradeId, targetOrder);
                        _logger.Info($"[Recovery] Re-tracked target order for {tradeId}");
                    }

                    if (targetOrder.OrderState != OrderState.Working && targetOrder.OrderState != OrderState.Accepted && targetOrder.OrderState != OrderState.Submitted && targetOrder.OrderState != OrderState.PartFilled)
                        throw new InvalidOperationException($"Target order is not modifiable (state: {targetOrder.OrderState})");

                    _orderTracker.TrackPendingModify(tradeId, new PendingModifyInfo(
                        newTp, targetOrder.Instrument, targetOrder.OrderAction, targetOrder.Quantity, isTarget: true));
                    _orderTracker.ExpectCancellation(targetOrder.Name);
                    account.Cancel(new[] { targetOrder });
                    modifiedAny = true;
                }

                if (!modifiedAny)
                {
                    _logger.Warning($"MODIFY ORDER: no valid stop_loss or take_profit provided for {tradeId}");
                    return false;
                }

                _logger.Info($"MODIFY PENDING: Cancelled orders for {tradeId}, replacements queued");
                _network?.SendTradeLog(tradeId, "NT:MODIFY", $"Modify cancel requested, replacements queued");
                return true;
            }
            catch (Exception ex)
            {
                var tradeId = payload?["trade_id"]?.ToString() ?? "unknown";
                _logger.Warning($"SL modify failed for {tradeId}: {ex.Message}");
                _network?.SendError("ninjatrader", "order_modify_failed", $"Failed to modify order {tradeId}: {ex.Message}");
                return false;
            }
        }

        private static Account ResolveAccount(string accountName)
        {
            if (string.IsNullOrEmpty(accountName))
            {
                if (Account.All.Count == 1) return Account.All.FirstOrDefault();
                return null;
            }
            return Account.All.FirstOrDefault(a => a.Name == accountName);
        }

        private Order FindStopOrderForTrade(Account account, string tradeId)
        {
            if (account == null) return null;
            string expectedName = $"Stop_{tradeId}";
            var orders = account.Orders.ToArray();
            foreach (var order in orders)
            {
                if (order.Name == expectedName &&
                    (order.OrderState == OrderState.Working || order.OrderState == OrderState.Accepted || order.OrderState == OrderState.Submitted || order.OrderState == OrderState.PartFilled))
                {
                    return order;
                }
            }
            return null;
        }

        private Order FindTargetOrderForTrade(Account account, string tradeId)
        {
            if (account == null) return null;
            string expectedName = $"Target_{tradeId}";
            var orders = account.Orders.ToArray();
            foreach (var order in orders)
            {
                if (order.Name == expectedName &&
                    (order.OrderState == OrderState.Working || order.OrderState == OrderState.Accepted || order.OrderState == OrderState.Submitted || order.OrderState == OrderState.PartFilled))
                {
                    return order;
                }
            }
            return null;
        }
    }
}
