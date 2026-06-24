using System;

namespace TradingBot.NinjaTrader.AddOn.Infrastructure
{
    public sealed class TickRateLimiter
    {
        private readonly int _maxTicksPerSecond;
        private DateTime _lastAllowedTime = DateTime.MinValue;
        private readonly object _lock = new object();

        public TickRateLimiter(int maxTicksPerSecond)
        {
            if (maxTicksPerSecond <= 0)
                throw new ArgumentException("Must be > 0", nameof(maxTicksPerSecond));
            _maxTicksPerSecond = maxTicksPerSecond;
        }

        public bool TryAllow()
        {
            lock (_lock)
            {
                var now = DateTime.UtcNow;
                if ((now - _lastAllowedTime).TotalMilliseconds >= 1000.0 / _maxTicksPerSecond)
                {
                    _lastAllowedTime = now;
                    return true;
                }
                return false;
            }
        }
    }
}
