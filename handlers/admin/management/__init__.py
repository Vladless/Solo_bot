from aiogram import Router


router = Router()

from . import (
    admins,  # noqa: F401
    database,  # noqa: F401
    domain,  # noqa: F401
    file_upload,  # noqa: F401
    import_3xui,  # noqa: F401
    maintenance,  # noqa: F401
    import_remnawave # noqa: F401
)
