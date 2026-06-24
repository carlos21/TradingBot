using System;
using TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.AddOn.Infrastructure
{
    public sealed class NinjatraderLogger : ILogger
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
                fullMessage += " | STACK: " + ex.StackTrace?.Replace("\n", " | ");
            Log("ERROR", fullMessage);
        }
        public void Success(string message) => Log("SUCCESS", message);
        public void Debug(string message) { /* No UI/output noise */ }

        private void Log(string level, string message)
        {
            _uiLog($"[{level}] {message}");
        }
    }
}
