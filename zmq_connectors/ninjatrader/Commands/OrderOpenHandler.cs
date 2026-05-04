// ═══════════════════════════════════════════════════════════════════════
// Commands Layer: OrderOpenHandler
// Handles ORDER_OPEN commands (Strategy Pattern)
// ═══════════════════════════════════════════════════════════════════════

using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using NinjaTrader.Cbi;

namespace NinjaTrader.NinjaScript.AddOns
{
    /// <summary>
    /// Handles ORDER_OPEN commands.
    /// Separates order creation logic from the main connector.
    /// Supports multi-account routing via "account" field in payload.
    /// </summary>
    internal sealed class OrderOpenHandler : ICommandHandler
    {
        public string CommandType => MessageType.OrderOpen;

        private readonly ZmqNetwork _network;
        private readonly ILogger _logger;
        private readonly Dictionary<string, Account> _accounts;
        private readonly string _instrument;
        private readonly IOrderTracker _orderTracker;

        public OrderOpenHandler(ZmqNetwork network, ILogger logger, Dictionary<string, Account> accounts, 
            string instrument, IOrderTracker orderTracker)
        {
            _network = network ?? throw new ArgumentNullException(nameof(network));
            _logger = logger ?? throw new ArgumentNullException(nameof(logger));
            _accounts = accounts ?? throw new ArgumentNullException(nameof(accounts));
            _instrument = instrument;
            _orderTracker = orderTracker ?? throw new ArgumentNullException(nameof(orderTracker));
        }

        public void Handle(JObject payload)
        {
            try
            {
                var (tradeId, direction, slPoints, rrRatio) = ParsePayload(payload);
                var accountName = payload?["account"]?.ToString();
                var account = ResolveAccount(accountName);

                if (account == null)
                    throw new InvalidOperationException($"No account available (requested: {accountName ?? "(default)"})");

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
                int qty = CalculatePositionSize(instrument, payload, slPoints, account);

                _logger.Info($"OPEN ORDER: {tradeId} {direction} {instrument.MasterInstrument.Name} x{qty} SL={slPoints}pt account={account.Name}");

                // Embed trade_id in order name for recovery after crash
                string entryOrderName = $"Entry_{tradeId}";
                
                var entryOrder = account.CreateOrder(
                    instrument, orderAction, OrderType.Market, OrderEntry.Automated, TimeInForce.Gtc,
                    qty, 0, 0, string.Empty, entryOrderName, DateTime.MinValue, null);

                if (entryOrder == null)
                    throw new InvalidOperationException("Failed to create entry order");

                _orderTracker.TrackEntry(tradeId, entryOrder);
                _orderTracker.TrackPendingEntry(tradeId, new PendingEntryInfo(direction, slPoints, rrRatio));

                account.Submit(new[] { entryOrder });

                _logger.Info($"OPEN ORDER SUBMITTED: {tradeId} {direction} {instrument.MasterInstrument.Name} x{qty} SL={slPoints}pt account={account.Name}");
                _network?.SendTradeLog(tradeId, "NT:ORDER", $"Market {direction} x{qty} submitted, bracket pending");
            }
            catch (Exception ex)
            {
                var tradeId = payload?["trade_id"]?.ToString() ?? "unknown";
                _logger.Error($"Order open failed for {tradeId}", ex);
                _network?.SendError("ninjatrader", "order_open_failed", $"Failed to open order {tradeId}: {ex.Message}");
                _orderTracker.RemoveTrade(tradeId);
            }
        }

        private Account ResolveAccount(string accountName)
        {
            if (string.IsNullOrEmpty(accountName))
            {
                // Default: use the first account if only one exists
                if (_accounts.Count == 1)
                {
                    foreach (var kvp in _accounts) return kvp.Value;
                }
                return null;
            }
            _accounts.TryGetValue(accountName, out var account);
            return account;
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

        private int CalculatePositionSize(Instrument instrument, JObject payload, double slPoints, Account account)
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
            else if (riskPct > 0 && account != null)
            {
                double balance = account.Get(AccountItem.CashValue, Currency.UsDollar);
                double risk = balance * riskPct / 100.0;
                qty = (int)Math.Round(risk / slRisk);
            }

            return Math.Max(1, Math.Min(qty, MaxQuantity));
        }

    }
}
