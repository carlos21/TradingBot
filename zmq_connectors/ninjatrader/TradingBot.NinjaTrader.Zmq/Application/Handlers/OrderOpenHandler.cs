using System;
using Newtonsoft.Json.Linq;
using TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.Zmq.Application.Handlers
{
    public sealed class OrderOpenHandler : ICommandHandler
    {
        public string CommandType => MessageType.OrderOpen;

        private readonly IZmqNetwork _network;
        private readonly ILogger _logger;
        private readonly IOrderTracker _orderTracker;
        private readonly ITradingMode _tradingMode;
        private readonly IAccountProvider _accountProvider;
        private readonly IInstrumentProvider _instrumentProvider;
        private readonly IOrderExecutionService _orderExecutionService;

        public OrderOpenHandler(
            IZmqNetwork network,
            ILogger logger,
            IOrderTracker orderTracker,
            ITradingMode tradingMode,
            IAccountProvider accountProvider,
            IInstrumentProvider instrumentProvider,
            IOrderExecutionService orderExecutionService)
        {
            _network = network ?? throw new ArgumentNullException(nameof(network));
            _logger = logger ?? throw new ArgumentNullException(nameof(logger));
            _orderTracker = orderTracker ?? throw new ArgumentNullException(nameof(orderTracker));
            _tradingMode = tradingMode ?? throw new ArgumentNullException(nameof(tradingMode));
            _accountProvider = accountProvider ?? throw new ArgumentNullException(nameof(accountProvider));
            _instrumentProvider = instrumentProvider ?? throw new ArgumentNullException(nameof(instrumentProvider));
            _orderExecutionService = orderExecutionService ?? throw new ArgumentNullException(nameof(orderExecutionService));
        }

        public bool Handle(JObject payload)
        {
            try
            {
                var (tradeId, direction, slPoints, rrRatio) = ParsePayload(payload);
                var accountName = payload?["account"]?.Value<string>();
                var instrumentName = payload?["instrument"]?.Value<string>();
                if (string.IsNullOrEmpty(instrumentName))
                    throw new InvalidOperationException("instrument is required in ORDER_OPEN payload");

                if (_tradingMode.IsSimulation)
                {
                    double entryPrice = payload?["entry_price"]?.Value<double>() ?? 0;
                    double stopLoss = payload?["stop_loss"]?.Value<double>() ?? 0;
                    double takeProfit = payload?["take_profit"]?.Value<double>() ?? 0;
                    int simQty = payload?["contracts"]?.Value<int>() ?? 1;

                    _logger.Info($"SIMULATE OPEN: {tradeId} {direction} {instrumentName} x{simQty} @ {entryPrice} SL={stopLoss} TP={takeProfit} account={accountName ?? "default"}");
                    _network.SendEntryFill(tradeId, entryPrice, stopLoss, takeProfit, account: accountName);
                    _network.SendTradeLog(tradeId, "NT:SIMULATE", $"Simulated entry fill {direction} x{simQty} @ {entryPrice}");
                    return true;
                }

                var account = ResolveAccount(accountName);
                if (account == null)
                    throw new InvalidOperationException($"No account available (requested: {accountName ?? "(default)"})");

                if (!account.HasConnection)
                    throw new InvalidOperationException($"Account '{account.Name}' has no broker connection.");

                if (_orderTracker.TryGetPendingEntry(tradeId, out _) || _orderTracker.TryGetEntry(tradeId, out _))
                {
                    _logger.Warning($"Duplicate place_order for {tradeId}, ignoring");
                    _network.SendTradeLog(tradeId, "NT:WARNING", "Duplicate place_order request ignored");
                    return true;
                }

                var instrument = _instrumentProvider.GetInstrument(instrumentName);
                if (instrument == null)
                    throw new InvalidOperationException($"Instrument '{instrumentName}' not found");

                bool isLong = direction == "long";
                var side = isLong ? OrderSide.Buy : OrderSide.SellShort;
                int qty = CalculatePositionSize(instrument, payload, slPoints, account);

                _logger.Info($"OPEN ORDER: {tradeId} {direction} {instrument.MasterInstrumentName} x{qty} SL={slPoints}pt account={account.Name}");

                var entryOrder = _orderExecutionService.CreateEntryOrder(instrument, account, side, qty, tradeId);
                if (entryOrder == null)
                    throw new InvalidOperationException("Failed to create entry order (returned null)");

                _orderTracker.TrackEntry(tradeId, entryOrder);
                _orderTracker.TrackPendingEntry(tradeId, new PendingEntryInfo(direction, slPoints, rrRatio));

                _orderExecutionService.SubmitOrder(entryOrder);

                _logger.Info($"OPEN ORDER SUBMITTED: {tradeId} {direction} {instrument.MasterInstrumentName} x{qty} SL={slPoints}pt account={account.Name}");
                _network.SendTradeLog(tradeId, "NT:ORDER", $"Market {direction} x{qty} submitted, bracket pending");
                return true;
            }
            catch (Exception ex)
            {
                var tradeId = payload?["trade_id"]?.ToString() ?? "unknown";
                _logger.Error($"Order open failed for {tradeId}", ex);
                _network.SendError("ninjatrader", "order_open_failed", $"Failed to open order {tradeId}: {ex.Message}");
                _orderTracker.RemoveTrade(tradeId);
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

        private static (string tradeId, string direction, double slPoints, double rrRatio) ParsePayload(JObject payload)
        {
            var tradeId = payload?["trade_id"]?.ToString();
            var direction = payload?["direction"]?.ToString();
            var slPoints = payload?["risk_points"]?.Value<double>() ?? 0;
            var rrRatio = payload?["rr_ratio"]?.Value<double>() ?? 2.0;

            if (string.IsNullOrEmpty(tradeId)) throw new ArgumentException("trade_id is required");
            if (string.IsNullOrEmpty(direction) || (direction != "long" && direction != "short"))
                throw new ArgumentException($"Invalid direction: {direction}");
            if (slPoints <= 0) throw new ArgumentException($"Invalid sl_points: {slPoints}");

            return (tradeId, direction, slPoints, rrRatio);
        }

        private int CalculatePositionSize(BrokerInstrument instrument, JObject payload, double slPoints, BrokerAccount account)
        {
            double riskUsd = payload?["risk_usd"]?.Value<double>() ?? 0;
            double riskPct = payload?["risk_pct"]?.Value<double>() ?? 0;
            double pointValue = instrument.PointValue;

            double slRisk = slPoints * pointValue;
            if (slRisk <= 0) return 1;

            const int MaxQuantity = 100;
            int qty = 1;

            if (riskUsd > 0)
                qty = (int)Math.Round(riskUsd / slRisk);
            else if (riskPct > 0)
            {
                double balance = account.CashValue;
                double risk = balance * riskPct / 100.0;
                qty = (int)Math.Round(risk / slRisk);
            }

            return Math.Max(1, Math.Min(qty, MaxQuantity));
        }
    }
}
