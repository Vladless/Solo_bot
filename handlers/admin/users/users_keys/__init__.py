from . import config, edit, lifecycle, operations  # noqa: F401 — trigger endpoint registration
from ._common import router
from .edit import handle_key_edit  # noqa: F401 — re-exported for users_tariffs.py


__all__ = ["router", "handle_key_edit"]
