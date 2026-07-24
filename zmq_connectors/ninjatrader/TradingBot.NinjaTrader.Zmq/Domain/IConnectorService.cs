namespace TradingBot.NinjaTrader.Zmq.Domain
{
    /// <summary>
    /// Control surface of the headless connector service, exposed to command
    /// handlers that need to affect the connection lifecycle (e.g. a
    /// platform-requested disconnect).
    /// </summary>
    public interface IConnectorService
    {
        void Disconnect(string reason = null);
    }
}
