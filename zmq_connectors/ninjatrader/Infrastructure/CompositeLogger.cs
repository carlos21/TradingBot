// ═══════════════════════════════════════════════════════════════════════
// Infrastructure Layer: Composite Logger
// Forwards every log call to multiple ILogger sinks. This lets the AddOn
// write to the UI window, the NinjaTrader Output tab, and a file without
// changing any business logic or the individual sinks.
// ═══════════════════════════════════════════════════════════════════════

using System;

namespace NinjaTrader.NinjaScript.AddOns
{
    /// <summary>
    /// Composite pattern: routes log calls to any number of underlying loggers.
    /// Keeps each sink single-purpose (UI vs file) and makes adding new sinks
    /// a matter of registration, not modification.
    /// </summary>
    internal sealed class CompositeLogger : ILogger
    {
        private readonly ILogger[] _sinks;

        public CompositeLogger(params ILogger[] sinks)
        {
            _sinks = sinks ?? throw new ArgumentNullException(nameof(sinks));
        }

        public void Info(string message)
        {
            foreach (var sink in _sinks)
                sink.Info(message);
        }

        public void Warning(string message)
        {
            foreach (var sink in _sinks)
                sink.Warning(message);
        }

        public void Error(string message, Exception ex = null)
        {
            foreach (var sink in _sinks)
                sink.Error(message, ex);
        }

        public void Success(string message)
        {
            foreach (var sink in _sinks)
                sink.Success(message);
        }

        public void Debug(string message)
        {
            foreach (var sink in _sinks)
                sink.Debug(message);
        }
    }
}
