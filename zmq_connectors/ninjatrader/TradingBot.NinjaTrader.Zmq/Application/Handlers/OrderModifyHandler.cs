using System;
using Newtonsoft.Json.Linq;
using TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.Zmq.Application.Handlers
{
    public sealed class OrderModifyHandler : ICommandHandler
    {
        public string CommandType => MessageType.OrderModify;

        private readonly IZmqNetwork _network;
        private readonly ILogger _logger;
        private readonly IOrderTracker _orderTracker;
        private readonly ITradeIdExtractor _tradeIdExtractor;
        private readonly ITradingMode _tradingMode;
        private readonly IAccountProvider _accountProvider;
        private readonly IInstrumentProvider _instrumentProvider;
        private readonly IOrderExecutionService _orderExecutionService;

        public OrderModifyHandler(
            IZmqNetwork network,
            ILogger logger,
            IOrderTracker orderTracker,
            ITradeIdExtractor tradeIdExtractor,
            ITradingMode tradingMode,
            IAccountProvider accountProvider,
            IInstrumentProvider instrumentProvider,
            IOrderExecutionService orderExecutionService)
        {
            _network = network ?? throw new ArgumentNullException(nameof(network));
            _logger = logger ?? throw new ArgumentNullException(nameof(logger));
            _orderTracker = orderTracker ?? throw new ArgumentNullException(nameof(orderTracker));
            _tradeIdExtractor = tradeIdExtractor ?? throw new ArgumentNullException(nameof(tradeIdExtractor));
            _tradingMode = tradingMode ?? throw new ArgumentNullException(nameof(tradingMode));
            _accountProvider = accountProvider ?? throw new ArgumentNullException(nameof(accountProvider));
            _instrumentProvider = instrumentProvider ?? throw new ArgumentNullException(nameof(instrumentProvider));
            _orderExecutionService = orderExecutionService ?? throw new ArgumentNullException(nameof(orderExecutionService));
        }

        public bool Handle(JObject payload)
        {
            try
            {
                _orderTracker.PurgeStaleModifies(TimeSpan.FromSeconds(60), _logger);

                var tradeId = payload?["trade_id"]?.ToString();
                var newSl = payload?["stop_loss"]?.Value<double>() ?? 0;
                var newTp = payload?["take_profit"]?.Value<double>() ?? 0;
                var accountName = payload?["account"]?.Value<string>();
                var instrumentName = payload?["instrument"]?.Value<string>();
                if (string.IsNullOrEmpty(instrumentName))
                    throw new InvalidOperationException("instrument is required in ORDER_MODIFY payload");

                if (string.IsNullOrEmpty(tradeId))
                    throw new ArgumentException("trade_id is required");
                if (newSl <= 0 && newTp <= 0)
                    throw new ArgumentException("At least one of stop_loss or take_profit must be provided");

                if (_tradingMode.IsSimulation)
                {
                    _logger.Info($"SIMULATE MODIFY: {tradeId} {instrumentName} new SL={newSl} new TP={newTp} account={accountName ?? "default"}");
                    _network.SendTradeLog(tradeId, "NT:SIMULATE", $"Simulated modify SL={newSl} TP={newTp}");
                    return true;
                }

                var account = ResolveAccount(accountName);
                if (account == null)
                    throw new InvalidOperationException($"No account available (requested: {accountName ?? "(default)"})");

                if (!account.HasConnection)
                    throw new InvalidOperationException($"Account '{account.Name}' has no broker connection.");

                _logger.Info($"MODIFY ORDER: {tradeId} new SL={newSl} new TP={newTp} account={account.Name}");

                var instrument = _instrumentProvider.GetInstrument(instrumentName);
                if (instrument == null)
                    throw new InvalidOperationException($"Instrument '{instrumentName}' not found");

                bool modifiedAny = false;

                if (newSl > 0)
                {
                    string slKey = tradeId + ":sl";
                    if (_orderTracker.TryGetPendingModify(slKey, out _))
                    {
                        _logger.Warning($"MODIFY REJECTED: {tradeId} SL modify already pending");
                        _network.SendTradeLog(tradeId, "NT:MODIFY", "SL modify rejected: previous SL modify still pending");
                    }
                    else
                    {
                        var stopOrder = FindStopOrderForTrade(account, tradeId);
                        if (stopOrder == null)
                            throw new InvalidOperationException($"Stop order not found for trade {tradeId}");

                        _orderTracker.TrackStopLoss(tradeId, stopOrder);
                        if (!stopOrder.IsWorking)
                            throw new InvalidOperationException($"Stop order is not modifiable (state: {stopOrder.OrderState})");

                        _orderTracker.TrackPendingModify(slKey, new PendingModifyInfo(newSl, instrument, stopOrder.OrderSide, stopOrder.Quantity));
                        _orderTracker.ExpectCancellation(stopOrder.Name);
                        _orderExecutionService.CancelOrder(stopOrder);
                        modifiedAny = true;
                    }
                }

                if (newTp > 0)
                {
                    string tpKey = tradeId + ":tp";
                    if (_orderTracker.TryGetPendingModify(tpKey, out _))
                    {
                        _logger.Warning($"MODIFY REJECTED: {tradeId} TP modify already pending");
                        _network.SendTradeLog(tradeId, "NT:MODIFY", "TP modify rejected: previous TP modify still pending");
                    }
                    else
                    {
                        var targetOrder = FindTargetOrderForTrade(account, tradeId);
                        if (targetOrder == null)
                            throw new InvalidOperationException($"Target order not found for trade {tradeId}");

                        _orderTracker.TrackTakeProfit(tradeId, targetOrder);
                        if (!targetOrder.IsWorking)
                            throw new InvalidOperationException($"Target order is not modifiable (state: {targetOrder.OrderState})");

                        _orderTracker.TrackPendingModify(tpKey, new PendingModifyInfo(newTp, instrument, targetOrder.OrderSide, targetOrder.Quantity, isTarget: true));
                        _orderTracker.ExpectCancellation(targetOrder.Name);
                        _orderExecutionService.CancelOrder(targetOrder);
                        modifiedAny = true;
                    }
                }

                if (!modifiedAny)
                {
                    _logger.Warning($"MODIFY ORDER: no valid stop_loss or take_profit provided for {tradeId}");
                    return false;
                }

                _logger.Info($"MODIFY PENDING: Cancelled orders for {tradeId}, replacements queued");
                _network.SendTradeLog(tradeId, "NT:MODIFY", "Modify cancel requested, replacements queued");
                return true;
            }
            catch (Exception ex)
            {
                var tradeId = payload?["trade_id"]?.ToString() ?? "unknown";
                _logger.Error($"Order modify failed for {tradeId}: {ex.Message}", ex);
                _network.SendError("ninjatrader", "order_modify_failed", $"Failed to modify order {tradeId}: {ex.Message}");
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

        private BrokerOrder FindStopOrderForTrade(BrokerAccount account, string tradeId)
        {
            if (_orderTracker.TryGetStopLoss(tradeId, out var tracked)) return tracked;
            return _orderExecutionService.FindOrderByName(account, $"Stop_{tradeId}");
        }

        private BrokerOrder FindTargetOrderForTrade(BrokerAccount account, string tradeId)
        {
            if (_orderTracker.TryGetTakeProfit(tradeId, out var tracked)) return tracked;
            return _orderExecutionService.FindOrderByName(account, $"Target_{tradeId}");
        }
    }
}
