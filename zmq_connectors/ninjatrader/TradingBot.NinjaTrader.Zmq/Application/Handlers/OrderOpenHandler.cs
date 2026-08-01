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
        private readonly IAccountProvider _accountProvider;
        private readonly IInstrumentProvider _instrumentProvider;
        private readonly IOrderExecutionService _orderExecutionService;

        public OrderOpenHandler(
            IZmqNetwork network,
            ILogger logger,
            IOrderTracker orderTracker,
            IAccountProvider accountProvider,
            IInstrumentProvider instrumentProvider,
            IOrderExecutionService orderExecutionService)
        {
            _network = network ?? throw new ArgumentNullException(nameof(network));
            _logger = logger ?? throw new ArgumentNullException(nameof(logger));
            _orderTracker = orderTracker ?? throw new ArgumentNullException(nameof(orderTracker));
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

                // Catastrophic-sizing guard: for MNQ the CME point value is $2 per full point.
                // If NT reports something else, every position-size calculation will be wrong.
                if (string.Equals(instrument.MasterInstrumentName, "MNQ", StringComparison.OrdinalIgnoreCase) &&
                    Math.Abs(instrument.PointValue - 2.0) > 0.01)
                {
                    _logger.Error($"CRITICAL: MNQ point value from NinjaTrader is {instrument.PointValue} but expected ~2.0. Refusing order to prevent catastrophic sizing.");
                    _network.SendError("ninjatrader", "point_value_mismatch", $"MNQ point value is {instrument.PointValue}, expected ~2.0");
                    return false;
                }

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

            int qty = 1;

            if (riskUsd > 0)
            {
                // Live mode should never send risk_usd; treat unexpected values as a safety-critical event.
                qty = (int)Math.Round(riskUsd / slRisk);
                _logger.Warning($"ORDER_OPEN: risk_usd={riskUsd} was sent (live mode should use risk_pct). Calculated qty={qty} for {account.Name}.");
            }
            else if (riskPct > 0)
            {
                double balance = account.CashValue;
                double risk = balance * riskPct / 100.0;
                qty = (int)Math.Round(risk / slRisk);
                _logger.Info($"POSITION SIZE: {instrument.MasterInstrumentName} balance={balance:C2} riskPct={riskPct}% slRisk={slRisk:C2} qty={qty}");
            }

            if (qty <= 0) qty = 1;

            // Catastrophic-risk guard: refuse if the implied dollar risk exceeds 5% of account.
            // This is NOT a hard position cap; it protects against config/unit bugs that would
            // blow up the account (e.g., wrong point value, stale balance, risk_usd too large).
            double impliedRisk = qty * slRisk;
            double maxRisk = account.CashValue * 0.05;
            if (maxRisk > 0 && impliedRisk > maxRisk)
            {
                throw new InvalidOperationException(
                    $"Calculated risk {impliedRisk:C2} ({qty} contracts × {slRisk:C2}) exceeds 5% of account ({maxRisk:C2}). " +
                    $"Refusing order. Check point_value ({pointValue}), account balance ({account.CashValue:C2}), and risk settings.");
            }

            return qty;
        }
    }
}
