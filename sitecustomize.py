from __future__ import annotations

import warnings


warnings.filterwarnings(
    "ignore",
    message=r'.*Field "model_custom_emoji_id" in UniqueGiftColors has conflict with protected namespace',
    category=UserWarning,
)

try:
    import aiogram.types

    from pydantic import ConfigDict

    unique_gift_colors = getattr(aiogram.types, "UniqueGiftColors", None)
    if unique_gift_colors is not None:
        cfg = getattr(unique_gift_colors, "model_config", None)
        base = dict(cfg) if cfg is not None else {}
        unique_gift_colors.model_config = ConfigDict(**base, protected_namespaces=())
except Exception:
    pass
