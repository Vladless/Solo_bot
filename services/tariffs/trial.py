def select_trial_tariff(tariffs: list[dict]) -> dict | None:
    """Выбирает последний активный пробный тариф."""
    return max(
        (tariff for tariff in tariffs if tariff.get("is_active", True)),
        key=lambda tariff: int(tariff.get("id") or 0),
        default=None,
    )
