using System;
using Newtonsoft.Json.Linq;
using TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.Zmq.Tests
{
    public static class TestDataFactory
    {
        public static BrokerInstrument Instrument(string name = "MNQ 09-25", string master = "MNQ", double pointValue = 2.0)
            => new BrokerInstrument(name, master, pointValue);

        public static BrokerAccount Account(string name = "Sim101", bool hasConnection = true, double cashValue = 50000)
            => new BrokerAccount(name, hasConnection, cashValue);

        public static BrokerOrder Order(
            string name = "Entry_test-1",
            string accountName = "Sim101",
            BrokerInstrument instrument = null,
            OrderType orderType = OrderType.Market,
            OrderSide side = OrderSide.Buy,
            OrderState state = OrderState.Working,
            int quantity = 2,
            int filled = 0,
            double avgFill = 0,
            double stopPrice = 0,
            double limitPrice = 0,
            double commission = 0)
            => new BrokerOrder(name, accountName, instrument ?? Instrument(), orderType, side, state, quantity, filled, avgFill, stopPrice, limitPrice, commission);

        public static Bar Bar(DateTime? time = null, double open = 20000, double high = 20010, double low = 19990, double close = 20005, long volume = 1000)
            => new Bar(time ?? new DateTime(2025, 1, 1, 12, 0, 0, DateTimeKind.Utc), open, high, low, close, volume);

        public static JObject RefreshPayload(string instrument = "MNQ 09-25", int? days = null)
        {
            var jo = new JObject { ["instrument"] = instrument };
            if (days.HasValue) jo["days"] = days.Value;
            return jo;
        }

        public static JObject OrderOpenPayload(
            string tradeId = "test-1",
            string direction = "long",
            string instrument = "MNQ 09-25",
            double riskPoints = 20,
            double rrRatio = 2,
            double? riskUsd = null,
            double? riskPct = null,
            string account = null,
            int? contracts = null,
            double? entryPrice = null,
            double? stopLoss = null,
            double? takeProfit = null)
        {
            var jo = new JObject
            {
                ["trade_id"] = tradeId,
                ["direction"] = direction,
                ["instrument"] = instrument,
                ["risk_points"] = riskPoints,
                ["rr_ratio"] = rrRatio
            };
            if (riskUsd.HasValue) jo["risk_usd"] = riskUsd.Value;
            if (riskPct.HasValue) jo["risk_pct"] = riskPct.Value;
            if (account != null) jo["account"] = account;
            if (contracts.HasValue) jo["contracts"] = contracts.Value;
            if (entryPrice.HasValue) jo["entry_price"] = entryPrice.Value;
            if (stopLoss.HasValue) jo["stop_loss"] = stopLoss.Value;
            if (takeProfit.HasValue) jo["take_profit"] = takeProfit.Value;
            return jo;
        }

        public static JObject OrderClosePayload(string tradeId = "test-1", string instrument = "MNQ 09-25", string account = null)
        {
            var jo = new JObject { ["trade_id"] = tradeId, ["instrument"] = instrument };
            if (account != null) jo["account"] = account;
            return jo;
        }

        public static JObject OrderModifyPayload(
            string tradeId = "test-1",
            string instrument = "MNQ 09-25",
            double? stopLoss = null,
            double? takeProfit = null,
            string account = null)
        {
            var jo = new JObject { ["trade_id"] = tradeId, ["instrument"] = instrument };
            if (stopLoss.HasValue) jo["stop_loss"] = stopLoss.Value;
            if (takeProfit.HasValue) jo["take_profit"] = takeProfit.Value;
            if (account != null) jo["account"] = account;
            return jo;
        }

        public static JObject SubscribePayload(string instrument = "MNQ 09-25")
            => new JObject { ["instrument"] = instrument };

        public static JObject AuditPayload(string instrument = null, int? barsBack = null)
        {
            var jo = new JObject();
            if (instrument != null) jo["instrument"] = instrument;
            if (barsBack.HasValue) jo["bars_back"] = barsBack.Value;
            return jo;
        }

        public static ZmqConfiguration Config(int historyDays = 30, int batchSize = 500)
            => new ZmqConfiguration(historyDays: historyDays, batchSize: batchSize);
    }
}
