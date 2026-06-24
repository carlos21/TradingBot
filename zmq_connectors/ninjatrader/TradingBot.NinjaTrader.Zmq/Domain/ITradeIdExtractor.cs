namespace TradingBot.NinjaTrader.Zmq.Domain
{
    /// <summary>
    /// Extracts trade IDs from order names.
    /// </summary>
    public interface ITradeIdExtractor
    {
        string ExtractTradeId(string orderName);
        bool IsEntryOrder(string orderName);
        bool IsStopOrder(string orderName);
        bool IsTargetOrder(string orderName);
        bool IsCloseOrder(string orderName);
    }
}
