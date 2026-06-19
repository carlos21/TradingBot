// ═══════════════════════════════════════════════════════════════════════
// Infrastructure Layer: Logger
// Adapter pattern: Adapts UI/Output logging to the domain ILogger interface.
// ═══════════════════════════════════════════════════════════════════════

using System;

namespace NinjaTrader.NinjaScript.AddOns
{
    /// <summary>
    /// Adapter pattern: adapts UI/output logging to the ILogger strategy.
    /// Formats each line with a level tag (e.g. [INFO], [ERROR]) and forwards
    /// it to the injected UI/output action. The sink itself is responsible
    /// for adding timestamps and persisting to a file.
    /// </summary>
    internal sealed class NinjatraderLogger : ILogger
    {
        private readonly Action<string> _uiLog;

        public NinjatraderLogger(Action<string> uiLog)
        {
            _uiLog = uiLog ?? throw new ArgumentNullException(nameof(uiLog));
        }

        public void Info(string message) => Log("INFO", message);
        public void Warning(string message) => Log("WARNING", message);
        public void Error(string message, Exception ex = null)
        {
            var fullMessage = ex != null ? $"{message} - {ex.Message}" : message;
            if (ex != null)
            {
                fullMessage += " | STACK: " + ex.StackTrace?.Replace("\n", " | ");
            }
            Log("ERROR", fullMessage);
        }
        public void Success(string message) => Log("SUCCESS", message);
        public void Debug(string message) { /* No UI/output noise for debug messages */ }

        private void Log(string level, string message)
        {
            _uiLog($"[{level}] {message}");
        }
    }
}
