import asyncio
import secrets
import threading

from dataclasses import dataclass, field
from weakref import WeakValueDictionary

from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage


@dataclass(frozen=True, slots=True)
class BroadcastRun:
    run_id: str
    owner_tg_id: int
    kind: str
    stop_event: threading.Event = field(default_factory=threading.Event)


_RUNS: dict[str, BroadcastRun] = {}
_RUNS_LOCK = threading.Lock()
_DRAFT_LOCKS: WeakValueDictionary[tuple[asyncio.AbstractEventLoop, int, object], asyncio.Lock] = WeakValueDictionary()


async def consume_broadcast_draft(state, expected_state: str, required_keys: tuple[str, ...]) -> dict | None:
    """Атомарно забирает подтверждённый черновик рассылки."""
    storage = state.storage
    if isinstance(storage, RedisStorage):
        state_key = storage.key_builder.build(state.key, "state")
        data_key = storage.key_builder.build(state.key, "data")
        script = (
            "if redis.call('GET',KEYS[1]) ~= ARGV[1] then return nil end; "
            "local data=redis.call('GET',KEYS[2]); "
            "if data then redis.call('DEL',KEYS[1],KEYS[2]) end; return data"
        )
        raw = await storage.redis.eval(script, 2, state_key, data_key, expected_state)
        if raw is None:
            return None
        data = storage.json_loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    elif isinstance(storage, MemoryStorage):
        key = (asyncio.get_running_loop(), id(storage), state.key)
        lock = _DRAFT_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _DRAFT_LOCKS[key] = lock
        async with lock:
            if await state.get_state() != expected_state:
                return None
            data = await state.get_data()
            await state.clear()
    else:
        raise TypeError("Хранилище черновиков рассылки не поддерживается")
    return data if isinstance(data, dict) and all(key in data for key in required_keys) else None


def start_broadcast_run(owner_tg_id: int, kind: str, *, run_id: str | None = None) -> BroadcastRun:
    """Регистрирует запущенную рассылку для остановки из другого потока."""
    run = BroadcastRun(run_id=run_id or secrets.token_hex(8), owner_tg_id=int(owner_tg_id), kind=kind)
    with _RUNS_LOCK:
        if run.run_id in _RUNS:
            raise ValueError("Рассылка уже выполняется")
        _RUNS[run.run_id] = run
    return run


def get_broadcast_run(run_id: str) -> BroadcastRun | None:
    """Находит выполняющуюся рассылку."""
    with _RUNS_LOCK:
        return _RUNS.get(run_id)


def stop_broadcast_run(run_id: str, owner_tg_id: int) -> bool:
    """Останавливает новые отправки рассылки её владельца."""
    with _RUNS_LOCK:
        run = _RUNS.get(run_id)
        if run is None or run.owner_tg_id != int(owner_tg_id):
            return False
        run.stop_event.set()
        return True


def finish_broadcast_run(run_id: str) -> None:
    """Удаляет завершённую рассылку из реестра."""
    with _RUNS_LOCK:
        _RUNS.pop(run_id, None)


async def wait_broadcast_delay(run: BroadcastRun | None, seconds: float) -> bool:
    """Ждёт указанное время с прерыванием по сигналу остановки."""
    if run is None:
        await asyncio.sleep(max(0.0, seconds))
        return False
    deadline = asyncio.get_running_loop().time() + max(0.0, seconds)
    while not run.stop_event.is_set():
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(0.05, remaining))
    return True
