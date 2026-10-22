from typing import Any


REDACTED_INPUT = "[скрыто]"
_SENSITIVE_STATES: set[str] = set()


def register_sensitive_states(*states: Any) -> None:
    """Помечает состояния с конфиденциальным вводом."""
    for state in states:
        name = getattr(state, "state", state)
        if isinstance(name, str) and name:
            _SENSITIVE_STATES.add(name)


def is_sensitive_input(data: dict[str, Any] | None) -> bool:
    """Определяет необходимость скрыть содержимое события."""
    if not data:
        return False
    return bool(data.get("sensitive_input")) or data.get("raw_state") in _SENSITIVE_STATES


async def resolve_sensitive_input(data: dict[str, Any]) -> bool:
    """Проверяет состояние до записи события в журналы."""
    if is_sensitive_input(data):
        return True
    if "raw_state" in data:
        return False
    state = data.get("state")
    if state is None:
        return False
    try:
        name = await state.get_state()
    except Exception:
        return True
    return name in _SENSITIVE_STATES
