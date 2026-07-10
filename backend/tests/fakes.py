from dataclasses import dataclass
from datetime import datetime

from src.analytics import AnalyticsReporter
from src.config.models import AccountConfig
from src.domain.models import LineData, TradeData
from src.domain.readiness.protocols import IExecutionContext
from src.domain.repositories import (
    AccountRepository,
)
from src.domain.repositories import (
    CredentialRepository as ICredentialRepository,
)
from src.infrastructure.data_sources.combined_datasource import CombinedDataSource
from src.notifier import Notifier
from src.services.trade_close_service import TradeEventPublisher
from src.services.trade_executor import TradeExecutor
from src.utils.app_logger import ILogger


class MutableTradingContext(IExecutionContext):
    """Test-only execution context that allows tests to toggle warmup/trading."""

    def __init__(self, trading_enabled=True, warmup=False):
        self.trading_enabled = trading_enabled
        self.warmup = warmup

    def is_trading_enabled(self) -> bool:
        return self.trading_enabled

    def is_warmup(self) -> bool:
        return self.warmup


class FakeLogger(ILogger):
    """No-op logger for unit tests."""

    def debug(self, message: str) -> None:
        pass

    def info(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        pass

    def error(self, message: str) -> None:
        pass

    def close(self) -> None:
        pass

class DummySocketIO:
    def __init__(self):
        self.events = []

    def emit(self, event, payload):
        self.events.append((event, payload))

    def start_background_task(self, target, *args, **kwargs):
        return target(*args, **kwargs)

    def run(self, app, **kwargs):
        pass

@dataclass
class _Line:
    line_id: str
    pair: str
    price: float
    creation_date: datetime

class FakeLineRepository:
    def __init__(self):
        self._seq = 0
        self._store = {}

    def list_lines(self, pair: str) -> list[LineData]:
        results = []
        for obj in self._store.values():
            if obj.pair == pair:
                results.append(LineData(
                    line_id=obj.line_id,
                    pair=obj.pair,
                    price=obj.price,
                    creation_date=obj.creation_date
                ))
        return results

    def insert_line(self, pair, price, creation_date=None) -> LineData:
        self._seq += 1
        lid = f"L{self._seq}"
        c_date = creation_date if creation_date else datetime.utcnow()
        obj = _Line(lid, pair, price, c_date)
        self._store[lid] = obj

        return LineData(
            line_id=obj.line_id,
            pair=obj.pair,
            price=obj.price,
            creation_date=obj.creation_date
        )

    def get_line(self, line_id: str) -> LineData | None:
        obj = self._store.get(line_id)
        if not obj:
            return None
        return LineData(
            line_id=obj.line_id,
            pair=obj.pair,
            price=obj.price,
            creation_date=obj.creation_date
        )

    def update_line(self, line_id: str, price: float) -> LineData:
        obj = self._store.get(line_id)
        if not obj:
            raise Exception(f"Line {line_id} not found")
        obj.price = price
        return LineData(
            line_id=obj.line_id,
            pair=obj.pair,
            price=obj.price,
            creation_date=obj.creation_date
        )

    def delete_line(self, line_id):
        if line_id in self._store:
            del self._store[line_id]

class FakeTradeRepository:
    def __init__(self):
        self._seq = 0
        self.inserted = []
        self.closed   = []

    def clear(self):
        """Wipe all data for a fresh scenario."""
        self._seq = 0
        self.inserted = []
        self.closed = []

    def clear_in_memory(self):
        """Alias used by __reset_all to clear only in-memory fakes between scenarios."""
        self.clear()

    def insert_trade(self, pair, trade_type, entry_price, stop_loss, take_profit, risk, entry_time, params=None, risk_dollars=None, risk_pct=None, account_balance=None, contracts=None, source=None, account=None, signal_id=None, trade_id=None):
        self._seq += 1
        trade_id = trade_id if trade_id else f"T{self._seq}"

        self.inserted.append({
            "trade_id": trade_id,
            "pair": pair,
            "type": trade_type,
            "entry": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "risk": risk,
            "risk_dollars": risk_dollars,
            "risk_pct": risk_pct,
            "account_balance": account_balance,
            "contracts": contracts,
            "entry_time": entry_time,
            "params": params,
            "source": source,
            "account": account,
            "signal_id": signal_id,
        })
        return TradeData(
            trade_id=trade_id,
            pair=pair,
            trade_type=trade_type,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk=risk,
            risk_dollars=risk_dollars,
            risk_pct=risk_pct,
            account_balance=account_balance,
            contracts=contracts,
            entry_time=entry_time,
            exit_price=None,
            exit_time=None,
            result=None,
            result_type=None,
            fees=None,
            pnl_usd=None,
            params=params,
            source=source,
            account=account,
            signal_id=signal_id,
        )

    def update_stop_loss(self, trade_id, new_stop_loss):
        for t in self.inserted:
            if t['trade_id'] == trade_id:
                t['stop_loss'] = new_stop_loss
                return

    def update_take_profit(self, trade_id, new_take_profit):
        for t in self.inserted:
            if t['trade_id'] == trade_id:
                t['take_profit'] = new_take_profit
                return

    def update_entry_price(self, trade_id, new_entry_price):
        for t in self.inserted:
            if t['trade_id'] == trade_id:
                t['entry'] = new_entry_price
                return

    def update_risk_fields(self, trade_id, risk, risk_dollars, risk_pct):
        for t in self.inserted:
            if t['trade_id'] == trade_id:
                t['risk'] = risk
                t['risk_dollars'] = risk_dollars
                t['risk_pct'] = risk_pct
                return

    def update_contracts(self, trade_id, contracts):
        for t in self.inserted:
            if t['trade_id'] == trade_id:
                t['contracts'] = contracts
                return

    def update_account_balance(self, trade_id, account_balance):
        for t in self.inserted:
            if t['trade_id'] == trade_id:
                t['account_balance'] = account_balance
                return

    def close_trade(self, trade_id, exit_price, exit_time, result, result_type=None, fees=None, pnl_usd=None):
        self.closed.append({
            "trade_id": trade_id,
            "exit_price": exit_price,
            "exit_time": exit_time,
            "result": result,
            "result_type": result_type,
            "fees": fees,
            "pnl_usd": pnl_usd,
        })

    def append_trade_log(self, trade_id: str, event: str, message: str) -> None:
        for t in self.inserted:
            if t['trade_id'] == trade_id:
                logs = t.setdefault('logs', [])
                logs.append({"ts": "", "event": event, "msg": message})
                return

    def get_trade_logs(self, trade_id: str) -> list:
        for t in self.inserted:
            if t['trade_id'] == trade_id:
                return t.get('logs', [])
        return []

    def get_trade(self, trade_id: str) -> TradeData | None:
        for t in self.inserted:
            if t['trade_id'] == trade_id:
                closed_info = next((c for c in self.closed if c['trade_id'] == trade_id), None)
                return TradeData(
                    trade_id=t['trade_id'],
                    pair=t['pair'],
                    trade_type=t['type'],
                    entry_price=t['entry'],
                    stop_loss=t['stop_loss'],
                    take_profit=t['take_profit'],
                    risk=t['risk'],
                    risk_dollars=t.get('risk_dollars'),
                    risk_pct=t.get('risk_pct'),
                    account_balance=t.get('account_balance'),
                    contracts=t.get('contracts'),
                    entry_time=t['entry_time'],
                    exit_price=closed_info['exit_price'] if closed_info else None,
                    exit_time=closed_info['exit_time'] if closed_info else None,
                    result=closed_info['result'] if closed_info else None,
                    result_type=closed_info.get('result_type') if closed_info else None,
                    fees=closed_info.get('fees') if closed_info else None,
                    pnl_usd=closed_info.get('pnl_usd') if closed_info else None,
                    params=t.get('params'),
                    source=t.get('source'),
                    account=t.get('account'),
                    signal_id=t.get('signal_id'),
                    created_at=datetime.utcnow()
                )
        return None

    def get_all_trades(self, pair: str) -> list[TradeData]:
        return self.list_trades(pair)

    def list_trades(self, pair: str) -> list[TradeData]:
        results = []
        for t in self.inserted:
            if t['pair'] != pair:
                continue

            closed_info = next((c for c in self.closed if c['trade_id'] == t['trade_id']), None)

            exit_price = closed_info['exit_price'] if closed_info else None
            exit_time  = closed_info['exit_time']  if closed_info else None
            result     = closed_info['result']     if closed_info else None
            result_type = closed_info.get('result_type') if closed_info else None

            results.append(TradeData(
                trade_id=t['trade_id'],
                pair=t['pair'],
                trade_type=t['type'],
                entry_price=t['entry'],
                stop_loss=t['stop_loss'],
                take_profit=t['take_profit'],
                risk=t['risk'],
                risk_dollars=t.get('risk_dollars'),
                risk_pct=t.get('risk_pct'),
                account_balance=t.get('account_balance'),
                contracts=t.get('contracts'),
                entry_time=t['entry_time'],
                exit_price=exit_price,
                exit_time=exit_time,
                result=result,
                result_type=result_type,
                fees=closed_info.get('fees') if closed_info else None,
                pnl_usd=closed_info.get('pnl_usd') if closed_info else None,
                params=t.get('params'),
                source=t.get('source'),
                account=t.get('account'),
                signal_id=t.get('signal_id'),
                created_at=datetime.utcnow()
            ))
        return results

    def delete_trade(self, trade_id: str) -> None:
        self.inserted = [t for t in self.inserted if t['trade_id'] != trade_id]
        self.closed = [c for c in self.closed if c['trade_id'] != trade_id]

    def delete_trades(self, trade_ids: list[str]) -> None:
        ids = set(trade_ids)
        missing = ids - {t['trade_id'] for t in self.inserted}
        if missing:
            raise Exception(f"Trade {sorted(missing)[0]} not found")
        self.inserted = [t for t in self.inserted if t['trade_id'] not in ids]
        self.closed = [c for c in self.closed if c['trade_id'] not in ids]


class FakeNotifier(Notifier):
    def __init__(self):
        self.messages = []

    def send(self, message: str) -> None:
        self.messages.append(message)


class FakeAnalyticsReporter(AnalyticsReporter):
    def __init__(self):
        self.exceptions = []
        self.trade_events = []
        self.signal_events = []
        self.contexts = {}

    def capture_exception(self, exc, context=None):
        self.exceptions.append((exc, context))

    def capture_trade_event(self, event_type, trade_data):
        self.trade_events.append((event_type, trade_data))

    def capture_signal_event(self, event_type, details):
        self.signal_events.append((event_type, details))

    def set_context(self, name, data):
        self.contexts[name] = data


class FakeTradeExecutor(TradeExecutor):
    def __init__(self):
        self.opens = []
        self.closes = []
        self.sl_updates = []

    def on_trade_open(self, trade):
        self.opens.append(trade)

    def on_trade_close(self, trade_id, exit_price):
        self.closes.append((trade_id, exit_price))

    def on_sl_update(self, trade_id, new_sl):
        self.sl_updates.append((trade_id, new_sl))


class FakeDataSource(CombinedDataSource):
    """Fake data source for testing."""

    def __init__(self, pair="MNQ", bars=None):
        self.pair = pair
        self._bars = bars or []
        self._callbacks = []
        self._paused = False

    def load_historical_bars(self, timeframe="1m") -> list[dict]:
        return self._bars

    def subscribe(self, callback, from_time=0):
        self._callbacks.append(callback)
        for bar in self._bars:
            if bar.get("time", 0) >= from_time:
                callback(bar)

    def pause(self):
        self._paused = True

    def add_bar(self, bar):
        self._bars.append(bar)
        for cb in self._callbacks:
            cb(bar)

    def set_bars(self, bars):
        self._bars = bars


class FakeTradeEventPublisher(TradeEventPublisher):
    """Records trade events for testing."""

    def __init__(self):
        self.closed_events = []
        self.updated_events = []

    def emit_trade_closed(self, trade_data: dict) -> None:
        self.closed_events.append(trade_data)

    def emit_trade_updated(self, trade_id: str, updates: dict) -> None:
        self.updated_events.append((trade_id, updates))


class FakeSettingsRepository:
    """In-memory settings repository for testing."""

    def __init__(self):
        self._store = {}

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def set(self, key: str, value: str, is_sensitive: bool = False) -> None:
        self._store[key] = value

    def get_all(self) -> dict[str, str]:
        return dict(self._store)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)


class FakeNtAccountRepository(AccountRepository):
    """In-memory account repository for testing."""

    def __init__(self):
        self._accounts = []

    def list_accounts(self) -> list[AccountConfig]:
        return list(self._accounts)

    def get_account(self, name: str) -> AccountConfig | None:
        for acct in self._accounts:
            if acct.name == name:
                return acct
        return None

    def upsert(self, name: str, risk_usd: float | None = None, risk_pct: float | None = None,
               rr_ratio: float | None = None, live_enabled: bool = True) -> None:
        for i, acct in enumerate(self._accounts):
            if acct.name == name:
                self._accounts[i] = AccountConfig(
                    name=name, risk_usd=risk_usd, risk_pct=risk_pct,
                    rr_ratio=rr_ratio, live_enabled=live_enabled,
                )
                return
        self._accounts.append(AccountConfig(
            name=name, risk_usd=risk_usd, risk_pct=risk_pct,
            rr_ratio=rr_ratio, live_enabled=live_enabled,
        ))

    def delete(self, name: str) -> None:
        self._accounts = [a for a in self._accounts if a.name != name]

    def clear_all(self) -> None:
        self._accounts = []


class FakeCredentialRepository(ICredentialRepository):
    """In-memory credential repository for testing."""

    def __init__(self):
        self._creds = []
        self._seq = 0

    def get_credential(self, service: str) -> tuple[str, str] | None:
        # Match the real repository: return the most recently saved credential.
        matches = [c for c in self._creds if c["service"] == service]
        if not matches:
            return None
        most_recent = max(matches, key=lambda c: c["_order"])
        return most_recent["username"], most_recent["password_encrypted"]

    def list_all(self) -> list[dict]:
        return [{"service": c["service"], "username": c["username"]} for c in self._creds]

    def save_credential(self, service: str, username: str, password_encrypted: str) -> None:
        self._seq += 1
        for c in self._creds:
            if c["service"] == service and c["username"] == username:
                c["password_encrypted"] = password_encrypted
                c["_order"] = self._seq
                return
        self._creds.append({
            "service": service,
            "username": username,
            "password_encrypted": password_encrypted,
            "_order": self._seq,
        })

    def delete_credential(self, service: str) -> None:
        self._creds = [c for c in self._creds if c["service"] != service]
