using TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.AddOn.Infrastructure
{
    /// <summary>
    /// Extracts trade IDs from NinjaTrader order naming conventions.
    /// </summary>
    public sealed class NtTradeIdExtractor : ITradeIdExtractor
    {
        public string ExtractTradeId(string orderName)
        {
            if (string.IsNullOrEmpty(orderName)) return null;

            if (orderName.StartsWith("Entry_"))
                return orderName.Substring(6);
            if (orderName.StartsWith("Stop_"))
                return orderName.Substring(5);
            if (orderName.StartsWith("Target_"))
                return orderName.Substring(7);
            if (orderName.StartsWith("Close_"))
                return orderName.Substring(6);

            return null;
        }

        public bool IsEntryOrder(string orderName) =>
            !string.IsNullOrEmpty(orderName) && orderName.StartsWith("Entry_");

        public bool IsStopOrder(string orderName) =>
            !string.IsNullOrEmpty(orderName) && orderName.StartsWith("Stop_");

        public bool IsTargetOrder(string orderName) =>
            !string.IsNullOrEmpty(orderName) && orderName.StartsWith("Target_");

        public bool IsCloseOrder(string orderName) =>
            !string.IsNullOrEmpty(orderName) && orderName.StartsWith("Close_");
    }
}
