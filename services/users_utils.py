from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Key
from handlers.keys.utils import build_key_ref, resolve_key


def build_admin_key_ref(client_id: str | None, email: str | None = None) -> str:
    return build_key_ref(client_id, email)


async def resolve_admin_key(session: AsyncSession, tg_id: int, key_ref: str | int | None) -> Key | None:
    return await resolve_key(session, tg_id, key_ref)
