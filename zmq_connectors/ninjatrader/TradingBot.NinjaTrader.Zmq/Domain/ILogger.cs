using System;

namespace TradingBot.NinjaTrader.Zmq.Domain
{
    /// <summary>
    /// Strategy pattern: Logging abstraction for testability.
    /// </summary>
    public interface ILogger
    {
        void Info(string message);
        void Warning(string message);
        void Error(string message, Exception ex = null);
        void Success(string message);
        void Debug(string message);
    }
}
