from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Identity
from logger import logger
from utils.cpu_tasks import hash_password


async def ensure_web_admin(session: AsyncSession) -> None:
    """Bootstrap/sync web-admin identity from config (WEB_ADMIN_LOGIN/WEB_ADMIN_PASSWORD).

    Вызов на старте API: если креды заданы в config.py — upsert Identity с bcrypt-хешем.
    Если пусто или переменных нет в config.py — no-op с предупреждением, сайт останется без админа.
    """
    from settings import config

    login = (getattr(config, "WEB_ADMIN_LOGIN", None) or "").strip()
    password = getattr(config, "WEB_ADMIN_PASSWORD", None) or ""

    if not (login and password):
        result = await session.execute(select(Identity).where(Identity.is_admin.is_(True)))
        has_admin = result.scalars().first() is not None
        if not has_admin:
            logger.warning(
                "[web-admin] Нет web-админа и WEB_ADMIN_LOGIN/WEB_ADMIN_PASSWORD "
                "не заданы в config.py. Сайт будет недоступен до создания админа."
            )
        return

    email = login.lower()
    result = await session.execute(select(Identity).where(Identity.email == email))
    identity = result.scalar_one_or_none()
    password_hash = hash_password(password)
    if identity is None:
        identity = Identity(
            email=email,
            password_hash=password_hash,
            is_admin=True,
            onboarding_stage="done",
        )
        session.add(identity)
        logger.info("[web-admin] created admin identity {}", email)
    else:
        identity.password_hash = password_hash
        identity.is_admin = True
        if identity.onboarding_completed_at is None and identity.onboarding_stage != "done":
            identity.onboarding_stage = "done"
        logger.info("[web-admin] synced password for {}", email)
