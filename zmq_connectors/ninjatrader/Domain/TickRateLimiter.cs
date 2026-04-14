// ═══════════════════════════════════════════════════════════════════════
// Domain Layer: Tick Rate Limiter
// Encapsulates throttling logic for outbound tick messages.
// ═══════════════════════════════════════════════════════════════════════

using System;

namespace NinjaTrader.NinjaScript.AddOns
{
    /// <summary>
    /// Thread-safe rate limiter for tick events.
    /// Allows a configurable maximum number of ticks per second.
    /// </summary>
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

        /// <summary>
        /// Returns true if the tick is allowed through based on the rate limit.
        /// </summary>
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
