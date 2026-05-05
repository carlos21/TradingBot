// ═══════════════════════════════════════════════════════════════════════
// Commands Layer: OrderCloseHandler
// Handles ORDER_CLOSE commands (Strategy Pattern)
// ═══════════════════════════════════════════════════════════════════════

using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json.Linq;
using NinjaTrader.Cbi;

namespace NinjaTrader.NinjaScript.AddOns
{
    /// <summary>
    /// Handles ORDER_CLOSE commands.
    /// Supports multi-account routing via "account" field in payload.
    /// </summary>
    internal sealed class OrderCloseHandler : ICommandHandler
    {
        public string CommandType => MessageType.OrderClose;

        private readonly ZmqNetwork _network;
        private readonly ILogger _logger;
        private readonly Dictionary<string, Account> _accounts;
        private readonly string _instrument;
        private readonly IOrderTracker _orderTracker;
        private readonly bool _simulate;

        public OrderCloseHandler(ZmqNetwork network, ILogger logger, Dictionary<string, Account> accounts, 
            string instrument, IOrderTracker orderTracker, bool simulate = false)
        {
            _network = network ?? throw new ArgumentNullException(nameof(network));
            _logger = logger ?? throw new ArgumentNullException(nameof(logger));
            _accounts = accounts ?? throw new ArgumentNullException(nameof(accounts));
            _instrument = instrument;
            _orderTracker = orderTracker ?? throw new ArgumentNullException(nameof(orderTracker));
            _simulate = simulate;
        }

        public bool Handle(JObject payload)
        {
            try
            {
                var tradeId = payload?["trade_id"]?.ToString();
                if (string.IsNullOrEmpty(tradeId))
                    throw new ArgumentException("trade_id is required");

                var accountName = payload?["account"]?.ToString();

                // ── SIMULATE MODE: Send fake exit fill instantly, NO account/broker lookup ──
                if (_simulate || TradingBotZmqConnector.E2ETestRunning)
                {
                    _logger.Info($"🧪 SIMULATE CLOSE: {tradeId} account={accountName ?? "default"}");
                    _network?.SendExitFill(tradeId, 0, "CLOSE", account: accountName);
                    _network?.SendTradeLog(tradeId, "NT:SIMULATE", "Simulated exit fill (close)");
                    return true;
                }

                var account = ResolveAccount(accountName);
                if (account == null)
                    throw new InvalidOperationException($"No account available (requested: {accountName ?? "(default)"})");

                // Guard: if trade is not tracked, it may already be closed
                bool hasTrackedEntry = _orderTracker.TryGetEntry(tradeId, out _);
                bool hasTrackedStop = _orderTracker.TryGetStopLoss(tradeId, out _);
                bool hasTrackedTarget = _orderTracker.TryGetTakeProfit(tradeId, out _);
                if (!hasTrackedEntry && !hasTrackedStop && !hasTrackedTarget)
                {
                    _logger.Warning($"[Close:{tradeId}] Trade not tracked — already closed or never opened. Ignoring.");
                    _network?.SendTradeLog(tradeId, "NT:WARNING", "Close ignored: trade not tracked");
                    return true;
                }

                // Guard: prevent duplicate close orders
                var existingClose = FindOrderByName(account, $"Close_{tradeId}");
                if (existingClose != null && IsWorking(existingClose))
                {
                    _logger.Warning($"[Close:{tradeId}] Close order already working. Ignoring duplicate.");
                    _network?.SendTradeLog(tradeId, "NT:WARNING", "Close ignored: already working");
                    return true;
                }

                _logger.Info($">>> CLOSE ORDER START: {tradeId} account={account.Name}");

                var instrument = Instrument.GetInstrument(_instrument);
                if (instrument == null)
                    throw new InvalidOperationException($"Instrument '{_instrument}' not found");

                // Get tracked orders for this trade
                var entryOrder = FindOrderByName(account, $"Entry_{tradeId}");
                var stopOrder = FindOrderByName(account, $"Stop_{tradeId}");
                var targetOrder = FindOrderByName(account, $"Target_{tradeId}");

                _logger.Info($"[Close:{tradeId}] Entry found={entryOrder != null} state={(entryOrder?.OrderState.ToString() ?? "null")} filledQty={entryOrder?.Filled ?? 0} totalQty={entryOrder?.Quantity ?? 0}");
                _logger.Info($"[Close:{tradeId}] Stop found={stopOrder != null} state={(stopOrder?.OrderState.ToString() ?? "null")}");
                _logger.Info($"[Close:{tradeId}] Target found={targetOrder != null} state={(targetOrder?.OrderState.ToString() ?? "null")}");

                // Cancel all working orders for this trade
                int cancelledCount = 0;
                
                if (entryOrder != null && IsWorking(entryOrder))
                {
                    try
                    {
                        account.Cancel(new[] { entryOrder });
                        _logger.Info($"[Close:{tradeId}] Cancelled entry order");
                        cancelledCount++;
                    }
                    catch (Exception cancelEx)
                    {
                        _logger.Warning($"[Close:{tradeId}] Entry cancel failed: {cancelEx.Message}");
                    }
                }
                
                if (stopOrder != null && IsWorking(stopOrder))
                {
                    _orderTracker.ExpectCancellation(stopOrder.Name);
                    try
                    {
                        account.Cancel(new[] { stopOrder });
                        _logger.Info($"[Close:{tradeId}] Cancelled stop order");
                        cancelledCount++;
                    }
                    catch (Exception cancelEx)
                    {
                        _logger.Warning($"[Close:{tradeId}] Stop cancel failed: {cancelEx.Message}");
                    }
                }
                
                if (targetOrder != null && IsWorking(targetOrder))
                {
                    _orderTracker.ExpectCancellation(targetOrder.Name);
                    try
                    {
                        account.Cancel(new[] { targetOrder });
                        _logger.Info($"[Close:{tradeId}] Cancelled target order");
                        cancelledCount++;
                    }
                    catch (Exception cancelEx)
                    {
                        _logger.Warning($"[Close:{tradeId}] Target cancel failed: {cancelEx.Message}");
                    }
                }

                // Check if we have a filled or partially filled position to close
                bool hasFilledPosition = entryOrder != null && (entryOrder.OrderState == OrderState.Filled || entryOrder.OrderState == OrderState.PartFilled);
                _logger.Info($"[Close:{tradeId}] hasFilledPosition={hasFilledPosition}");

                if (hasFilledPosition)
                {
                    // Submit closing market order for the FILLED quantity only
                    var closeQty = entryOrder.Filled;
                    if (closeQty <= 0) closeQty = entryOrder.Quantity;
                    var closeAction = entryOrder.OrderAction == OrderAction.Buy ? OrderAction.Sell : OrderAction.BuyToCover;

                    _logger.Info($"[Close:{tradeId}] Creating close order: action={closeAction} qty={closeQty} instrument={instrument.MasterInstrument.Name}");

                    var closeOrder = account.CreateOrder(
                        instrument,
                        closeAction,
                        OrderType.Market,
                        OrderEntry.Automated,
                        TimeInForce.Gtc,
                        closeQty,
                        0, 0, string.Empty, $"Close_{tradeId}", DateTime.MinValue, null);
                    
                    if (closeOrder != null)
                    {
                        _logger.Info($"[Close:{tradeId}] Close order created successfully. Name={closeOrder.Name} State={closeOrder.OrderState}");

                        // Track the close order so we know when it fills
                        _orderTracker.TrackCloseOrder(tradeId, closeOrder);
                        _logger.Info($"[Close:{tradeId}] Tracked close order in OrderTracker");

                        try
                        {
                            account.Submit(new[] { closeOrder });
                            _logger.Success($"[Close:{tradeId}] SUBMITTED close order to broker ({closeAction} {closeQty} contracts)");
                        }
                        catch (Exception submitEx)
                        {
                            _logger.Error($"[Close:{tradeId}] FAILED to submit close order: {submitEx.Message}", submitEx);
                        }
                    }
                    else
                    {
                        _logger.Error($"[Close:{tradeId}] CreateOrder returned NULL — close order was not created");
                    }
                }
                else if (cancelledCount == 0)
                {
                    _logger.Warning($"[Close:{tradeId}] No working orders or filled position found. Nothing to close.");
                    // Remove from tracking since there's nothing to close
                    _orderTracker.RemoveTrade(tradeId);
                }
                else
                {
                    // Entry was cancelled but stop/target were working - just removed them
                    _logger.Info($"[Close:{tradeId}] Cancelled {cancelledCount} working orders. No filled position to close.");
                    _orderTracker.RemoveTrade(tradeId);
                }
                
                _logger.Info($">>> CLOSE ORDER END: {tradeId}");
                _network?.SendTradeLog(tradeId, "NT:CLOSE", $"Close command executed ({cancelledCount} orders cancelled)");
                return true;
            }
            catch (Exception ex)
            {
                var tradeId = payload?["trade_id"]?.ToString() ?? "unknown";
                _logger.Error($">>> CLOSE ORDER FAILED for {tradeId}: {ex.Message}", ex);
                _network?.SendError("ninjatrader", "order_close_failed", $"Failed to close order {tradeId}: {ex.Message}");
                return false;
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

        private Order FindOrderByName(Account account, string orderName)
        {
            if (account == null || string.IsNullOrEmpty(orderName)) return null;
            
            // Snapshot to avoid collection-modified-during-enumeration
            var orders = account.Orders.ToArray();
            foreach (var order in orders)
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
                   order.OrderState == OrderState.Submitted ||
                   order.OrderState == OrderState.Initialized;
        }
    }
}
