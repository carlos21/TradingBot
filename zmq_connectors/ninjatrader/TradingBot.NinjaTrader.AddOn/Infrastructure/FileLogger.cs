using System;
using System.IO;
using TradingBot.NinjaTrader.Zmq.Domain;

namespace TradingBot.NinjaTrader.AddOn.Infrastructure
{
    public sealed class FileLogger : ILogger, IDisposable
    {
        private readonly string _logDirectory;
        private readonly object _lock = new object();
        private StreamWriter _writer;
        private DateTime _currentDate;
        private bool _fileAccessFailed;

        public FileLogger(string logDirectory)
        {
            _logDirectory = logDirectory ?? throw new ArgumentNullException(nameof(logDirectory));
            Directory.CreateDirectory(_logDirectory);
        }

        public void Info(string message) => Write("INFO", message);
        public void Warning(string message) => Write("WARNING", message);
        public void Error(string message, Exception ex = null) => Write("ERROR", ex != null ? $"{message} - {ex.Message}" : message);
        public void Success(string message) => Write("SUCCESS", message);
        public void Debug(string message) { }

        private void Write(string level, string message)
        {
            var now = DateTime.Now;
            var line = $"{now:yyyy-MM-dd HH:mm:ss.fff} [{level}] {message}";
            lock (_lock)
            {
                if (_fileAccessFailed) return;

                try
                {
                    EnsureWriter(now.Date);
                    _writer?.WriteLine(line);
                    _writer?.Flush();
                }
                catch (IOException)
                {
                    // File is locked by another process (likely a stale NinjaTrader instance).
                    // Disable file logging for this session to avoid repeated crashes.
                    _fileAccessFailed = true;
                    _writer?.Dispose();
                    _writer = null;
                }
            }
        }

        private void EnsureWriter(DateTime date)
        {
            if (_writer != null && _currentDate == date) return;
            _writer?.Dispose();
            _currentDate = date;
            var path = Path.Combine(_logDirectory, $"TradingBotZMQ_{date:yyyy-MM-dd}.log");
            _writer = new StreamWriter(path, append: true) { AutoFlush = true };
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
