// ═══════════════════════════════════════════════════════════════════════
// Infrastructure Layer: Logger
// Adapter pattern: Adapts UI logging to ILogger interface
// ═══════════════════════════════════════════════════════════════════════

using System;
using NinjaTrader.Code;

namespace NinjaTrader.NinjaScript.AddOns
{
    /// <summary>
    /// Adapter pattern: Adapts UI logging to ILogger interface.
    /// Also outputs to NinjaTrader's Print output.
    /// </summary>
    internal sealed class NinjatraderLogger : ILogger
    {
        private readonly Action<string> _uiLog;

        public NinjatraderLogger(Action<string> uiLog)
        {
            _uiLog = uiLog ?? throw new ArgumentNullException(nameof(uiLog));
        }

        public void Info(string message) => Log("INFO", message);
        public void Warning(string message) => Log("WARNING", message, isWarning: true);
        public void Error(string message, Exception ex = null)
        {
            var fullMessage = ex != null ? $"{message} - {ex.Message}" : message;
            if (ex != null)
            {
                fullMessage += " | STACK: " + ex.StackTrace?.Replace("\n", " | ");
            }
            Log("ERROR", fullMessage, isError: true);
        }
        public void Success(string message) => Log("SUCCESS", message, isSuccess: true);
        public void Debug(string message) { /* No UI noise for debug messages */ }

        private void Log(string level, string message, bool isError = false, bool isWarning = false, bool isSuccess = false)
        {
            string prefix;
            if (isError) prefix = "*** ERROR *** ";
            else if (isWarning) prefix = "*** WARNING *** ";
            else if (isSuccess) prefix = "*** SUCCESS *** ";
            else prefix = "";

            _uiLog(prefix + message);
            if (isError || isWarning)
            {
                string outputMsg = isError
                    ? "[TradingBot ZMQ ERROR] " + message
                    : "[TradingBot ZMQ WARNING] " + message;
                // Output.Process must run on the UI thread in NinjaTrader
                NinjaTrader.Core.Globals.RandomDispatcher.BeginInvoke(new Action(() =>
                    Output.Process(outputMsg, PrintTo.OutputTab1)));
            }
        }
    }
}
