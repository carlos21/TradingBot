import pandas as pd
from datetime import datetime
from typing import Optional


class TradeSimulator:
    def __init__(
        self,
        pair: str,
        start_date: str,
        end_date: str,
        bars_csv: str,
        trades_csv: str,
        risk_reward: float
    ):
        """
        :param pair: symbol to simulate, e.g. "EURUSD"
        :param start_date: "YYYY-MM-DD" inclusive
        :param end_date:   "YYYY-MM-DD" inclusive
        :param bars_csv:   path to bars CSV with columns Date,Time,Open,High,Low,Close
        :param trades_csv: path to trades CSV with columns pair, entry_time, exit_time, entry_price, stop_loss, result, trade_type
        :param risk_reward: the RR ratio to test, e.g. 3.0
        """
        self.pair = pair
        self.start_date = datetime.fromisoformat(start_date).date()
        self.end_date   = datetime.fromisoformat(end_date).date()
        self.bars_csv   = bars_csv
        self.trades_csv = trades_csv
        self.rr         = risk_reward

        self.bars_df   = self._load_bars()
        self.trades_df = self._load_trades()

    def _load_bars(self) -> pd.DataFrame:
        df = pd.read_csv(self.bars_csv)
        df['datetime'] = pd.to_datetime(df['Date'] + ' ' + df['Time'])
        df['timestamp'] = df['datetime'].astype(int) // 10**9
        return df.sort_values('timestamp')

    def _load_trades(self) -> pd.DataFrame:
        df = pd.read_csv(self.trades_csv, parse_dates=['entry_time', 'exit_time'])
        # filter by pair and entry_date
        df = df[df['pair'] == self.pair].copy()
        df['entry_date'] = df['entry_time'].dt.date
        mask = (df['entry_date'] >= self.start_date) & (df['entry_date'] <= self.end_date)
        return df[mask]

    def _simulate(self):
        wins = losses = 0
        pnl  = 0.0
        sc_wins = sc_losses = 0
        sc_pnl = 0.0

        for _, t in self.trades_df.iterrows():
            entry     = t['entry_price']
            sl        = t['stop_loss']
            risk      = abs(entry - sl)
            direction = 1 if t['trade_type'] == 'long' else -1

            # current PnL & counts
            curr_pnl = t['result'] * risk
            pnl  += curr_pnl
            if t['result'] > 0: wins += 1
            else:               losses += 1

            # scenario TP
            tp = entry + direction * self.rr * risk

            # scan future bars
            future = self.bars_df[self.bars_df['timestamp'] >= int(t['entry_time'].timestamp())]
            outcome = None
            for __, bar in future.iterrows():
                low, high = bar['Low'], bar['High']
                if direction == 1:
                    if low  <= sl:
                        outcome = -risk; break
                    if high >= tp:
                        outcome =  self.rr * risk; break
                else:
                    if high >= sl:
                        outcome = -risk; break
                    if low  <= tp:
                        outcome =  self.rr * risk; break
            if outcome is None:
                outcome = -risk

            sc_pnl  += outcome
            if outcome > 0: sc_wins += 1
            else:           sc_losses += 1

        return {
            'current': {
                'pnl': pnl,
                'wins': wins,
                'losses': losses
            },
            'scenario': {
                'rr': self.rr,
                'pnl': sc_pnl,
                'wins': sc_wins,
                'losses': sc_losses
            }
        }

    def run(self):
        """Execute the simulation and print results."""
        results = self._simulate()

        cur = results['current']
        sc  = results['scenario']
        print(f"CURRENT  (orig RR):   PnL = {cur['pnl']:.2f}, Wins = {cur['wins']}, Losses = {cur['losses']}")
        print(f"SCENARIO (RR = {sc['rr']:.1f}): PnL = {sc['pnl']:.2f}, Wins = {sc['wins']}, Losses = {sc['losses']}")


# Example usage:
if __name__ == "__main__":
    sim = TradeSimulator(
        pair="EURUSD",
        start_date="2019-01-01",
        end_date="2019-01-31",
        bars_csv="csvs/EURUSD_2019.csv",
        trades_csv="exported_trades.csv",
        risk_reward=3.0
    )
    sim.run()