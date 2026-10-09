from collections.abc import Mapping
from typing import Any


def validate_traffic_notification_settings(config: Mapping[str, Any]) -> None:
    """Проверяет пороги уведомлений о трафике."""
    for key in (
        "EXPIRY_24H_SKIP_TRIAL_ENABLED",
        "TRAFFIC_WARNING_ENABLED",
        "TRAFFIC_WARNING_LEVEL_1_ENABLED",
        "TRAFFIC_WARNING_LEVEL_2_ENABLED",
        "TRAFFIC_EXHAUSTED_ENABLED",
    ):
        if key in config and not isinstance(config[key], bool):
            raise ValueError(key)
    enabled = []
    for number, default in ((1, 20), (2, 10)):
        prefix = f"TRAFFIC_WARNING_LEVEL_{number}"
        value = config.get(f"{prefix}_PERCENT", default)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
            raise ValueError(f"{prefix}_PERCENT")
        if bool(config.get(f"{prefix}_ENABLED", True)):
            enabled.append(value)
    if len(enabled) == 2 and enabled[0] <= enabled[1]:
        raise ValueError("TRAFFIC_WARNING_LEVEL_1_PERCENT")


def traffic_notification_levels(config: Mapping[str, Any]) -> tuple[int, ...]:
    """Возвращает включённые пороги остатка трафика."""
    validate_traffic_notification_settings(config)
    if not bool(config.get("TRAFFIC_WARNING_ENABLED", False)):
        return ()
    levels = [
        int(config.get(f"TRAFFIC_WARNING_LEVEL_{number}_PERCENT", default))
        for number, default in ((1, 20), (2, 10))
        if bool(config.get(f"TRAFFIC_WARNING_LEVEL_{number}_ENABLED", True))
    ]
    if bool(config.get("TRAFFIC_EXHAUSTED_ENABLED", True)):
        levels.append(0)
    return tuple(sorted(set(levels), reverse=True))
