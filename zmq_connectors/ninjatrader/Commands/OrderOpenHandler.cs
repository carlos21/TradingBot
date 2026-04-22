// ═══════════════════════════════════════════════════════════════════════
// Commands Layer: OrderOpenHandler
// Handles ORDER_OPEN commands (Strategy Pattern)
// ═══════════════════════════════════════════════════════════════════════

using System;
using Newtonsoft.Json.Linq;
using NinjaTrader.Cbi;

namespace NinjaTrader.NinjaScript.AddOns
{
    /// <summary>
    /// Handles ORDER_OPEN commands.
    /// Separates order creation logic from the main connector.
    /// </summary>
    internal sealed class OrderOpenHandler : ICommandHandler
    {
        public string CommandType => MessageType.OrderOpen;

        private readonly ZmqNetwork _network;
        private readonly ILogger _logger;
        private readonly Account _account;
        private readonly string _instrument;
        private readonly IOrderTracker _orderTracker;

        public OrderOpenHandler(ZmqNetwork network, ILogger logger, Account account, 
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
                var (tradeId, direction, slPoints, rrRatio) = ParsePayload(payload);

                if (_account == null)
                    throw new InvalidOperationException("No account available");

                if (_orderTracker.TryGetPendingEntry(tradeId, out _) || _orderTracker.TryGetEntry(tradeId, out _))
                {
                    _logger.Warning($"Duplicate place_order for {tradeId}, ignoring");
                    _network?.SendTradeLog(tradeId, "NT:WARNING", "Duplicate place_order request ignored");
                    return;
                }

                var instrument = Instrument.GetInstrument(_instrument);
                if (instrument == null)
                    throw new InvalidOperationException($"Instrument '{_instrument}' not found");

                bool isLong = direction == "long";
                var orderAction = isLong ? OrderAction.Buy : OrderAction.SellShort;
                int qty = CalculatePositionSize(instrument, payload, slPoints);

                string atmStrategyName = GetAtmStrategyName(slPoints);

                _logger.Info($"OPEN ORDER: {tradeId} {direction} {instrument.MasterInstrument.Name} x{qty} SL={slPoints}pt");

                // Embed trade_id in order name for recovery after crash
                string entryOrderName = $"Entry_{tradeId}";
                
                var entryOrder = _account.CreateOrder(
                    instrument, orderAction, OrderType.Market, OrderEntry.Automated, TimeInForce.Gtc,
                    qty, 0, 0, string.Empty, entryOrderName, DateTime.MinValue, null);

                if (entryOrder == null)
                    throw new InvalidOperationException("Failed to create entry order");

                _orderTracker.TrackEntry(tradeId, entryOrder);
                _orderTracker.TrackAtmStrategy(tradeId, atmStrategyName);
                _orderTracker.TrackPendingEntry(tradeId, new PendingEntryInfo(direction, slPoints, rrRatio, atmStrategyName));

                _account.Submit(new[] { entryOrder });

                _logger.Info($"OPEN ORDER SUBMITTED: {tradeId} {direction} {instrument.MasterInstrument.Name} x{qty} SL={slPoints}pt");
                _network?.SendTradeLog(tradeId, "NT:ORDER", $"Market {direction} x{qty} submitted, ATM '{atmStrategyName}' pending");
            }
            catch (Exception ex)
            {
                var tradeId = payload?["trade_id"]?.ToString() ?? "unknown";
                _logger.Error($"Order open failed for {tradeId}", ex);
                _network?.SendError("ninjatrader", "order_open_failed", $"Failed to open order {tradeId}: {ex.Message}");
                _orderTracker.RemoveTrade(tradeId);
            }
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

        private int CalculatePositionSize(Instrument instrument, JObject payload, double slPoints)
        {
            double riskUsd = payload?["risk_usd"]?.Value<double>() ?? 0;
            double riskPct = payload?["risk_pct"]?.Value<double>() ?? 0;
            double pointValue = instrument.MasterInstrument.PointValue;

            double slRisk = slPoints * pointValue;
            if (slRisk <= 0) return 1;

            const int MaxQuantity = 100;  // Safety clamp to prevent catastrophic sizing
            int qty = 1;

            if (riskUsd > 0)
                qty = (int)Math.Round(riskUsd / slRisk);
            else if (riskPct > 0 && _account != null)
            {
                double balance = _account.Get(AccountItem.CashValue, Currency.UsDollar);
                double risk = balance * riskPct / 100.0;
                qty = (int)Math.Round(risk / slRisk);
            }

            return Math.Max(1, Math.Min(qty, MaxQuantity));
        }

        private static string GetAtmStrategyName(double slPoints)
        {
            if (slPoints <= 15.0) return "TA_MNQ_15pt";
            if (slPoints <= 20.0) return "TA_MNQ_20pt";
            if (slPoints <= 30.0) return "TA_MNQ_30pt";
            return "TA_MNQ_40pt";
        }
    }
}
