using System;
using TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.AddOn.Infrastructure
{
    public sealed class CompositeLogger : ILogger
    {
        private readonly ILogger _primary;
        private readonly ILogger _secondary;

        public CompositeLogger(ILogger primary, ILogger secondary)
        {
            _primary = primary ?? throw new ArgumentNullException(nameof(primary));
            _secondary = secondary ?? throw new ArgumentNullException(nameof(secondary));
        }

        public void Info(string message)
        {
            _primary.Info(message);
            _secondary.Info(message);
        }

        public void Warning(string message)
        {
            _primary.Warning(message);
            _secondary.Warning(message);
        }

        public void Error(string message, Exception ex = null)
        {
            _primary.Error(message, ex);
            _secondary.Error(message, ex);
        }

        public void Success(string message)
        {
            _primary.Success(message);
            _secondary.Success(message);
        }

        public void Debug(string message)
        {
            _primary.Debug(message);
            _secondary.Debug(message);
        }
    }
}
