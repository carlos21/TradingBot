"""Analytics service for calculating trade statistics and chart data."""
from collections import defaultdict
from typing import Any

from src.analytics import (
    DistributionData,
    TimeSeriesData,
    TradeDetail,
    TradeStatistics,
)
from src.domain.repositories import TradeRepository


class AccountStatistics:
    """Per-account trade statistics."""

    def __init__(
        self,
        account: str,
        total_trades: int,
        winning_trades: int,
        losing_trades: int,
        win_rate: float,
        total_pnl_usd: float,
        avg_pnl_usd: float,
    ):
        self.account = account
        self.total_trades = total_trades
        self.winning_trades = winning_trades
        self.losing_trades = losing_trades
        self.win_rate = win_rate
        self.total_pnl_usd = total_pnl_usd
        self.avg_pnl_usd = avg_pnl_usd

    def to_dict(self) -> dict[str, Any]:
        return {
            "account": self.account,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": round(self.win_rate, 4),
            "total_pnl_usd": round(self.total_pnl_usd, 2),
            "avg_pnl_usd": round(self.avg_pnl_usd, 2),
        }


class AnalyticsService:
    """Service for trade analytics calculations."""

    def __init__(self, trade_repository: TradeRepository):
        self._repo = trade_repository

    def calculate_statistics(self, pair: str) -> TradeStatistics:
        """Calculate trade statistics for a pair."""
        trades = self._repo.get_all_trades(pair)

        total = len(trades)
        open_trades = [t for t in trades if t.exit_time is None]
        closed_trades = [t for t in trades if t.exit_time is not None]

        if not closed_trades:
            return TradeStatistics(
                total_trades=total,
                open_trades=len(open_trades),
                winning_trades=0,
                losing_trades=0,
                win_rate=0.0,
                total_pnl=0.0,
                total_pnl_usd=0.0,
                avg_pnl=0.0,
                avg_pnl_usd=0.0,
                avg_win=0.0,
                avg_loss=0.0,
                profit_factor=0.0,
                avg_r_multiple=0.0,
            )

        winning = [t for t in closed_trades if (t.result or 0) > 0]
        losing = [t for t in closed_trades if (t.result or 0) <= 0]

        # R-multiple calculations
        total_pnl_r = sum(t.result or 0 for t in closed_trades)
        win_rate = len(winning) / len(closed_trades) if closed_trades else 0.0
        avg_pnl_r = total_pnl_r / len(closed_trades) if closed_trades else 0.0
        avg_win_r = sum(t.result or 0 for t in winning) / len(winning) if winning else 0.0
        avg_loss_r = sum(t.result or 0 for t in losing) / len(losing) if losing else 0.0

        gross_profit = sum(t.result or 0 for t in winning)
        gross_loss = abs(sum(t.result or 0 for t in losing))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

        # Dollar PnL: prefer stored pnl_usd (includes fees), fallback for legacy trades
        total_pnl_usd = sum(
            t.pnl_usd if t.pnl_usd is not None
            else (t.result or 0) * (t.risk_dollars if t.risk_dollars else t.risk)
            for t in closed_trades
        )
        avg_pnl_usd = total_pnl_usd / len(closed_trades) if closed_trades else 0.0

        # Calculate average R-multiple
        r_multiples = [(t.result or 0) for t in closed_trades]
        avg_r = sum(r_multiples) / len(r_multiples) if r_multiples else 0.0

        # Calculate average monthly profit
        avg_profit_monthly = self._calculate_avg_profit_monthly(closed_trades)

        # Enhanced KPIs
        max_dd, max_dd_pct = self._calculate_max_drawdown(closed_trades)
        expectancy = self._calculate_expectancy(win_rate, avg_win_r, avg_loss_r)
        max_win_streak, max_loss_streak, current_streak, streak_type = self._calculate_streaks(closed_trades)

        return TradeStatistics(
            total_trades=total,
            open_trades=len(open_trades),
            winning_trades=len(winning),
            losing_trades=len(losing),
            win_rate=win_rate,
            total_pnl=total_pnl_r,
            total_pnl_usd=total_pnl_usd,
            avg_pnl=avg_pnl_r,
            avg_pnl_usd=avg_pnl_usd,
            avg_win=avg_win_r,
            avg_loss=avg_loss_r,
            profit_factor=profit_factor,
            avg_r_multiple=avg_r,
            avg_profit_monthly=avg_profit_monthly,
            max_drawdown=max_dd,
            max_drawdown_pct=max_dd_pct,
            expectancy=expectancy,
            max_consecutive_wins=max_win_streak,
            max_consecutive_losses=max_loss_streak,
            current_streak=current_streak,
            current_streak_type=streak_type,
        )

    def get_equity_curve(self, pair: str) -> TimeSeriesData:
        """Generate equity curve data (cumulative PnL over time)."""
        trades = self._repo.get_all_trades(pair)
        closed_trades = [t for t in trades if t.exit_time is not None]

        # Sort by exit time
        closed_trades.sort(key=lambda t: t.exit_time)

        if not closed_trades:
            return TimeSeriesData(labels=[], values=[])

        labels = []
        values = []
        cumulative = 0.0

        # Track current month for labeling
        current_month = None
        month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                       "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

        for trade in closed_trades:
            cumulative += trade.result or 0

            # Label at month boundaries (when month changes)
            trade_month = (trade.exit_time.year, trade.exit_time.month)
            if trade_month != current_month:
                current_month = trade_month
                month_label = f"{month_names[trade.exit_time.month - 1]} {trade.exit_time.year}"
                labels.append(month_label)
            else:
                labels.append("")

            values.append(cumulative)

        return TimeSeriesData(labels=labels, values=values)

    def get_trades_by_hour(self, pair: str) -> DistributionData:
        """Get trade distribution by hour of day."""
        trades = self._repo.get_all_trades(pair)

        hour_counts = defaultdict(int)
        for trade in trades:
            hour = trade.entry_time.hour
            hour_counts[hour] += 1

        # Sort by hour
        sorted_hours = sorted(hour_counts.items())
        labels = [f"{h:02d}:00" for h, _ in sorted_hours]
        values = [count for _, count in sorted_hours]

        return DistributionData(labels=labels, values=values)

    def get_trades_by_day(self, pair: str) -> DistributionData:
        """Get trade distribution by day of week."""
        trades = self._repo.get_all_trades(pair)

        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        day_counts = defaultdict(int)

        for trade in trades:
            day_idx = trade.entry_time.weekday()
            day_counts[day_idx] += 1

        labels = [day_names[i] for i in range(7)]
        values = [day_counts.get(i, 0) for i in range(7)]

        return DistributionData(labels=labels, values=values)

    def get_result_distribution(self, pair: str) -> DistributionData:
        """Get distribution of trade results (SL, TP, manual)."""
        trades = self._repo.get_all_trades(pair)
        closed_trades = [t for t in trades if t.exit_time is not None]

        result_counts = defaultdict(int)
        for trade in closed_trades:
            result_type = trade.result_type or "OTHER"
            result_counts[result_type] += 1

        # Map to short labels as requested (TP, SL, SP, BE)
        label_map = {
            "SL": "SL",
            "TP": "TP",
            "BE": "BE",
            "SP": "SP",
        }

        labels = [label_map.get(k, k) for k in result_counts]
        values = list(result_counts.values())

        return DistributionData(labels=labels, values=values)

    def get_monthly_pnl(self, pair: str) -> TimeSeriesData:
        """Get PnL grouped by month."""
        trades = self._repo.get_all_trades(pair)
        closed_trades = [t for t in trades if t.exit_time is not None]

        monthly_pnl = defaultdict(float)
        for trade in closed_trades:
            month_key = trade.exit_time.strftime("%Y-%m")
            monthly_pnl[month_key] += trade.result or 0

        # Sort by month
        sorted_months = sorted(monthly_pnl.items())
        labels = [m for m, _ in sorted_months]
        values = [pnl for _, pnl in sorted_months]

        return TimeSeriesData(labels=labels, values=values)

    def get_pnl_distribution(self, pair: str, bins: int = 10) -> DistributionData:
        """Get distribution of PnL values (histogram)."""
        trades = self._repo.get_all_trades(pair)
        closed_trades = [t for t in trades if t.exit_time is not None]

        if not closed_trades:
            return DistributionData(labels=[], values=[])

        results = [t.result or 0 for t in closed_trades]
        min_pnl = min(results)
        max_pnl = max(results)

        if min_pnl == max_pnl:
            return DistributionData(labels=[f"{min_pnl:.0f}"], values=[len(results)])

        bin_size = (max_pnl - min_pnl) / bins
        bin_counts = [0] * bins

        for r in results:
            bin_idx = min(int((r - min_pnl) / bin_size), bins - 1)
            bin_counts[bin_idx] += 1

        labels = [f"{min_pnl + i * bin_size:.0f}" for i in range(bins)]

        return DistributionData(labels=labels, values=bin_counts)

    def get_trade_detail(self, trade_id: str) -> TradeDetail | None:
        """Get full trade details including logs."""
        trade = self._repo.get_trade(trade_id)
        if not trade:
            return None

        logs = self._repo.get_trade_logs(trade_id)

        return TradeDetail(
            trade_id=trade.trade_id,
            pair=trade.pair,
            trade_type=trade.trade_type,
            entry_price=trade.entry_price,
            stop_loss=trade.stop_loss,
            take_profit=trade.take_profit,
            risk=trade.risk,
            risk_dollars=trade.risk_dollars,
            risk_pct=trade.risk_pct,
            contracts=trade.contracts,
            entry_time=trade.entry_time.timestamp(),
            exit_price=trade.exit_price,
            exit_time=trade.exit_time.timestamp() if trade.exit_time else None,
            result=trade.result,
            result_type=trade.result_type,
            fees=trade.fees,
            pnl_usd=trade.pnl_usd,
            status="closed" if trade.exit_time else "open",
            logs=logs,
        )

    def _calculate_max_drawdown(self, closed_trades: list) -> tuple[float, float]:
        """Calculate max drawdown in R and as percentage of peak equity."""
        if not closed_trades:
            return 0.0, 0.0

        # Sort by exit time for equity curve
        sorted_trades = sorted(closed_trades, key=lambda t: t.exit_time or t.entry_time)

        peak = 0.0
        max_dd = 0.0
        equity = 0.0

        for trade in sorted_trades:
            equity += trade.result or 0
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd

        max_dd_pct = (max_dd / peak * 100) if peak > 0 else 0.0
        return max_dd, max_dd_pct

    def _calculate_expectancy(self, win_rate: float, avg_win: float, avg_loss: float) -> float:
        """Calculate expectancy: (Win% × Avg Win) − (Loss% × |Avg Loss|)."""
        loss_rate = 1.0 - win_rate
        return (win_rate * avg_win) - (loss_rate * abs(avg_loss))

    def _calculate_streaks(self, closed_trades: list) -> tuple[int, int, int, str]:
        """Calculate max consecutive wins/losses and current streak.
        Returns (max_win_streak, max_loss_streak, current_streak, streak_type).
        """
        if not closed_trades:
            return 0, 0, 0, ""

        # Sort by exit time
        sorted_trades = sorted(closed_trades, key=lambda t: t.exit_time or t.entry_time)

        max_win_streak = 0
        max_loss_streak = 0
        current_win_streak = 0
        current_loss_streak = 0

        for trade in sorted_trades:
            result = trade.result or 0
            if result > 0:
                current_win_streak += 1
                current_loss_streak = 0
                if current_win_streak > max_win_streak:
                    max_win_streak = current_win_streak
            else:
                current_loss_streak += 1
                current_win_streak = 0
                if current_loss_streak > max_loss_streak:
                    max_loss_streak = current_loss_streak

        current_streak = current_win_streak if current_win_streak > 0 else current_loss_streak
        streak_type = "win" if current_win_streak > 0 else "loss"

        return max_win_streak, max_loss_streak, current_streak, streak_type

    def _calculate_avg_profit_monthly(self, closed_trades: list) -> float:
        """Calculate average profit per month based on closed trades."""
        if not closed_trades:
            return 0.0

        # Group PnL by month
        monthly_pnl = defaultdict(float)
        for trade in closed_trades:
            if trade.exit_time:
                month_key = trade.exit_time.strftime("%Y-%m")
                pnl_usd = trade.pnl_usd if trade.pnl_usd is not None else \
                    (trade.result or 0) * (trade.risk_dollars if trade.risk_dollars else trade.risk)
                monthly_pnl[month_key] += pnl_usd

        if not monthly_pnl:
            return 0.0

        # Calculate average across all months with trades
        return sum(monthly_pnl.values()) / len(monthly_pnl)

    def get_paginated_trades(
        self,
        pair: str,
        limit: int = 50,
        offset: int = 0
    ) -> dict[str, Any]:
        """Get paginated trade list."""
        trades = self._repo.get_all_trades(pair)

        # Sort by entry time descending
        trades.sort(key=lambda t: t.entry_time, reverse=True)

        total = len(trades)
        paginated = trades[offset:offset + limit]

        trade_list = []
        for t in paginated:
            trade_list.append({
                "trade_id": t.trade_id,
                "pair": t.pair,
                "type": t.trade_type,
                "entry": t.entry_price,
                "stop_loss": t.stop_loss,
                "take_profit": t.take_profit,
                "risk": t.risk,
                "risk_dollars": t.risk_dollars,
                "risk_pct": t.risk_pct,
                "pnl_usd": t.pnl_usd,
                "entry_time": t.entry_time.timestamp(),
                "exit_price": t.exit_price,
                "exit_time": t.exit_time.timestamp() if t.exit_time else None,
                "result": t.result,
                "result_type": t.result_type,
                "status": "closed" if t.exit_time else "open",
            })

        return {
            "trades": trade_list,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def get_account_analytics(self, pair: str) -> list[dict[str, Any]]:
        """Get per-account trade statistics."""
        trades = self._repo.get_all_trades(pair)
        closed_trades = [t for t in trades if t.exit_time is not None]

        # Group by account (fallback to 'default' if no account)
        by_account: dict[str, list] = defaultdict(list)
        for trade in closed_trades:
            acct = trade.account or 'default'
            by_account[acct].append(trade)

        results = []
        for account, acct_trades in sorted(by_account.items()):
            winning = [t for t in acct_trades if (t.result or 0) > 0]
            losing = [t for t in acct_trades if (t.result or 0) <= 0]

            total_pnl_usd = sum(
                t.pnl_usd if t.pnl_usd is not None
                else (t.result or 0) * (t.risk_dollars if t.risk_dollars else t.risk)
                for t in acct_trades
            )

            stats = AccountStatistics(
                account=account,
                total_trades=len(acct_trades),
                winning_trades=len(winning),
                losing_trades=len(losing),
                win_rate=len(winning) / len(acct_trades) if acct_trades else 0.0,
                total_pnl_usd=total_pnl_usd,
                avg_pnl_usd=total_pnl_usd / len(acct_trades) if acct_trades else 0.0,
            )
            results.append(stats.to_dict())

        return results
