from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Setting

PROVIDERS_ORDER: dict[str, int] = {}


async def load_providers_order(session: AsyncSession) -> None:
    stmt = select(Setting).where(Setting.key == "PROVIDERS_ORDER")
    result = await session.execute(stmt)
    setting = result.scalar_one_or_none()

    PROVIDERS_ORDER.clear()
    if setting and isinstance(setting.value, dict):
        PROVIDERS_ORDER.update({k: int(v) for k, v in setting.value.items()})
    await session.flush()


async def update_providers_order(session: AsyncSession, new_order: dict[str, int]) -> None:
    stmt = select(Setting).where(Setting.key == "PROVIDERS_ORDER")
    result = await session.execute(stmt)
    setting = result.scalar_one_or_none()

    if setting is None:
        setting = Setting(
            key="PROVIDERS_ORDER",
            value=new_order,
            description="Порядок отображения платёжных провайдеров",
        )
        session.add(setting)
    else:
        setting.value = new_order

    await session.commit()

    PROVIDERS_ORDER.clear()
    PROVIDERS_ORDER.update(new_order)
