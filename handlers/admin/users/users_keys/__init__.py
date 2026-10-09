from . import config, edit, lifecycle, operations
from ._common import router
from .edit import handle_key_edit


__all__ = ["router", "handle_key_edit"]
