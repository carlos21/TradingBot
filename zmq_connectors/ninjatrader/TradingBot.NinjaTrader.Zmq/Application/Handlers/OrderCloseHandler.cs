using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.Zmq.Application.Handlers
{
    public sealed class OrderCloseHandler : ICommandHandler
    {
        public string CommandType => MessageType.OrderClose;

        private readonly IZmqNetwork _network;
        private readonly ILogger _logger;
        private readonly IOrderTracker _orderTracker;
        private readonly ITradeIdExtractor _tradeIdExtractor;
        private readonly IAccountProvider _accountProvider;
        private readonly IInstrumentProvider _instrumentProvider;
        private readonly IOrderExecutionService _orderExecutionService;

        public OrderCloseHandler(
            IZmqNetwork network,
            ILogger logger,
            IOrderTracker orderTracker,
            ITradeIdExtractor tradeIdExtractor,
            IAccountProvider accountProvider,
            IInstrumentProvider instrumentProvider,
            IOrderExecutionService orderExecutionService)
        {
            _network = network ?? throw new ArgumentNullException(nameof(network));
            _logger = logger ?? throw new ArgumentNullException(nameof(logger));
            _orderTracker = orderTracker ?? throw new ArgumentNullException(nameof(orderTracker));
            _tradeIdExtractor = tradeIdExtractor ?? throw new ArgumentNullException(nameof(tradeIdExtractor));
            _accountProvider = accountProvider ?? throw new ArgumentNullException(nameof(accountProvider));
            _instrumentProvider = instrumentProvider ?? throw new ArgumentNullException(nameof(instrumentProvider));
            _orderExecutionService = orderExecutionService ?? throw new ArgumentNullException(nameof(orderExecutionService));
        }

        public bool Handle(JObject payload)
        {
            try
            {
                var tradeId = payload?["trade_id"]?.ToString();
                if (string.IsNullOrEmpty(tradeId))
                    throw new ArgumentException("trade_id is required");

                var accountName = payload?["account"]?.Value<string>();
                var instrumentName = payload?["instrument"]?.Value<string>();
                if (string.IsNullOrEmpty(instrumentName))
                    throw new InvalidOperationException("instrument is required in ORDER_CLOSE payload");

                var account = ResolveAccount(accountName);
                if (account == null)
                    throw new InvalidOperationException($"No account available (requested: {accountName ?? "(default)"})");

                if (!account.HasConnection)
                    throw new InvalidOperationException($"Account '{account.Name}' has no broker connection.");

                bool hasTrackedEntry = _orderTracker.TryGetEntry(tradeId, out _);
                bool hasTrackedStop = _orderTracker.TryGetStopLoss(tradeId, out _);
                bool hasTrackedTarget = _orderTracker.TryGetTakeProfit(tradeId, out _);
                if (!hasTrackedEntry && !hasTrackedStop && !hasTrackedTarget)
                {
                    _logger.Warning($"[Close:{tradeId}] Trade not tracked — already closed or never opened. Ignoring.");
                    _network.SendTradeLog(tradeId, "NT:WARNING", "Close ignored: trade not tracked");
                    return true;
                }

                var entryName = $"Entry_{tradeId}";
                var stopName = $"Stop_{tradeId}";
                var targetName = $"Target_{tradeId}";

                var entryOrder = _orderExecutionService.FindOrderByName(account, entryName);
                var stopOrder = _orderExecutionService.FindOrderByName(account, stopName);
                var targetOrder = _orderExecutionService.FindOrderByName(account, targetName);

                int cancelledCount = 0;

                if (entryOrder != null && entryOrder.IsWorking)
                {
                    _orderExecutionService.CancelOrder(entryOrder);
                    _logger.Info($"[Close:{tradeId}] Cancelled entry order");
                    cancelledCount++;
                }

                if (stopOrder != null && stopOrder.IsWorking)
                {
                    _orderTracker.ExpectCancellation(stopOrder.Name);
                    _orderExecutionService.CancelOrder(stopOrder);
                    _logger.Info($"[Close:{tradeId}] Cancelled stop order");
                    cancelledCount++;
                }

                if (targetOrder != null && targetOrder.IsWorking)
                {
                    _orderTracker.ExpectCancellation(targetOrder.Name);
                    _orderExecutionService.CancelOrder(targetOrder);
                    _logger.Info($"[Close:{tradeId}] Cancelled target order");
                    cancelledCount++;
                }

                bool hasFilledPosition = entryOrder != null && (entryOrder.OrderState == OrderState.Filled || entryOrder.OrderState == OrderState.PartFilled);
                _logger.Info($"[Close:{tradeId}] hasFilledPosition={hasFilledPosition}");

                if (hasFilledPosition)
                {
                    var closeQty = entryOrder.Filled > 0 ? entryOrder.Filled : entryOrder.Quantity;
                    var closeSide = entryOrder.OrderSide == OrderSide.Buy ? OrderSide.Sell : OrderSide.BuyToCover;

                    var instrument = _instrumentProvider.GetInstrument(instrumentName);
                    if (instrument == null)
                        throw new InvalidOperationException($"Instrument '{instrumentName}' not found");

                    var closeOrder = _orderExecutionService.CreateMarketCloseOrder(instrument, account, closeSide, closeQty, tradeId);
                    if (closeOrder != null)
                    {
                        _orderTracker.TrackCloseOrder(tradeId, closeOrder);
                        _orderExecutionService.SubmitOrder(closeOrder);
                        _logger.Info($"[Close:{tradeId}] SUBMITTED close order to broker ({closeSide} {closeQty} contracts)");
                    }
                    else
                    {
                        _logger.Error($"[Close:{tradeId}] CreateOrder returned NULL — close order was not created");
                    }
                }
                else if (cancelledCount == 0)
                {
                    _logger.Warning($"[Close:{tradeId}] No working orders or filled position found. Removing trade.");
                    _orderTracker.RemoveTrade(tradeId);
                }
                else
                {
                    _logger.Info($"[Close:{tradeId}] Cancelled {cancelledCount} working orders. Marking close-pending in case entry fills despite cancel.");
                    _orderTracker.MarkClosePending(tradeId);
                }

                _logger.Info($">>> CLOSE ORDER END: {tradeId}");
                _network.SendTradeLog(tradeId, "NT:CLOSE", "Close command executed");
                return true;
            }
            catch (Exception ex)
            {
                var tradeId = payload?["trade_id"]?.ToString() ?? "unknown";
                _logger.Error($">>> CLOSE ORDER FAILED for {tradeId}: {ex.Message}", ex);
                _network.SendError("ninjatrader", "order_close_failed", $"Failed to close order {tradeId}: {ex.Message}");
                return false;
            }
        }

        private BrokerAccount ResolveAccount(string accountName)
        {
            var accounts = _accountProvider.GetAccounts();
            if (string.IsNullOrEmpty(accountName))
            {
                if (accounts.Count == 1) return accounts[0];
                return null;
            }
            foreach (var account in accounts)
                if (account.Name == accountName) return account;
            return null;
        }
    }
}
