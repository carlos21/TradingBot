// ═══════════════════════════════════════════════════════════════════════
// Infrastructure Layer: File Logger Sink
// Writes log lines to a daily rolling file so Python and NT logs can be
// compared side-by-side. Implements the domain ILogger strategy.
// ═══════════════════════════════════════════════════════════════════════

using System;
using System.IO;
using NinjaTrader.Code;

namespace NinjaTrader.NinjaScript.AddOns
{
    /// <summary>
    /// File-system logger sink. Each line is prefixed with a full datetime
    /// and appended to a daily log file under the configured directory.
    /// </summary>
    internal sealed class FileLogger : ILogger
    {
        private readonly string _logDirectory;
        private readonly string _fileNamePrefix;
        private readonly object _lock = new object();

        private StreamWriter _writer;
        private string _currentFilePath;
        private DateTime _currentFileDate;

        public FileLogger(string logDirectory, string fileNamePrefix = "TradingBotZMQ")
        {
            _logDirectory = logDirectory ?? throw new ArgumentNullException(nameof(logDirectory));
            _fileNamePrefix = fileNamePrefix ?? throw new ArgumentNullException(nameof(fileNamePrefix));
        }

        public void Info(string message) => WriteLine(message);
        public void Warning(string message) => WriteLine(message);
        public void Error(string message, Exception ex = null) => WriteLine(message);
        public void Success(string message) => WriteLine(message);
        public void Debug(string message) => WriteLine(message);

        private void WriteLine(string message)
        {
            try
            {
                string line = $"{DateTime.Now:yyyy-MM-dd HH:mm:ss.fff} {message}";
                lock (_lock)
                {
                    EnsureWriter();
                    _writer?.WriteLine(line);
                    _writer?.Flush();
                }
            }
            catch (Exception ex)
            {
                // Never crash the AddOn because of a logging failure.
                // Fall back to NinjaTrader's output window.
                string fallback = $"[TradingBotZMQ FILE LOG FAILED] {ex.Message}";
                NinjaTrader.Core.Globals.RandomDispatcher.BeginInvoke(new Action(() =>
                    Output.Process(fallback, PrintTo.OutputTab1)));
            }
        }

        private void EnsureWriter()
        {
            DateTime today = DateTime.Now.Date;
            if (_writer != null && _currentFileDate == today)
                return;

            _writer?.Dispose();
            _writer = null;

            Directory.CreateDirectory(_logDirectory);
            _currentFileDate = today;
            _currentFilePath = Path.Combine(
                _logDirectory,
                $"{_fileNamePrefix}_{today:yyyy-MM-dd}.log");

            _writer = new StreamWriter(_currentFilePath, append: true)
            {
                AutoFlush = false
            };
        }

        public void Dispose()
        {
            lock (_lock)
            {
                _writer?.Dispose();
                _writer = null;
            }
        }
    }
}
