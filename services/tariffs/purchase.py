from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from database.notifications import check_cold_lead_discount, check_hot_lead_discount
from database.tariffs import get_tariffs, get_tariffs_for_cluster
from hooks.processors import process_purchase_tariff_group_override
from logger import logger
from services.clusters import select_cluster

from .visibility import filter_visible_tariffs


@dataclass
class PurchaseTariffs:
    """Тарифы новой покупки с учётом кластера и условий клиента."""

    cluster_name: str
    group_code: str | None
    tariffs: list[dict[str, Any]]
    discount_info: dict[str, Any] | None
    state_discount_info: dict[str, Any] | None


async def resolve_purchase_tariffs(
    session: AsyncSession,
    user_ref: int,
    *,
    cluster_name: str | None = None,
) -> PurchaseTariffs:
    """Выбирает доступные клиенту тарифы новой подписки."""
    if cluster_name is None:
        cluster_name = (await select_cluster(session)).cluster_name
    tariffs = await get_tariffs_for_cluster(session, cluster_name)
    group_code = tariffs[0].get("group_code") if tariffs else None
    discount_info = None
    state_discount_info = None

    if group_code:
        original_group_code = group_code
        discount_info = await check_hot_lead_discount(session, user_ref)
        if not (discount_info and discount_info.get("available")):
            cold_discount_info = await check_cold_lead_discount(session, user_ref)
            if cold_discount_info and cold_discount_info.get("available"):
                discount_info = cold_discount_info

        if discount_info and discount_info.get("available"):
            group_code = discount_info["tariff_group"]
            state_discount_info = discount_info

        override_result = await process_purchase_tariff_group_override(
            chat_id=user_ref,
            admin=False,
            session=session,
            original_group=group_code,
        )
        if override_result:
            group_code = override_result["override_group"]
            logger.info(f"[PURCHASE] Тарифная группа переопределена хуком: {group_code}")
            if override_result.get("discount_info"):
                state_discount_info = override_result["discount_info"]

        tariffs_data = await get_tariffs(session, group_code=group_code, with_subgroup_weights=True)
        tariffs = await filter_visible_tariffs(
            session, user_ref, [tariff for tariff in tariffs_data["tariffs"] if tariff.get("is_active")]
        )

        if not tariffs and discount_info and discount_info.get("available"):
            logger.warning(f"[PURCHASE] Нет тарифов со скидкой {group_code}, fallback на {original_group_code}")
            group_code = original_group_code
            tariffs_data = await get_tariffs(session, group_code=group_code, with_subgroup_weights=True)
            tariffs = await filter_visible_tariffs(
                session, user_ref, [tariff for tariff in tariffs_data["tariffs"] if tariff.get("is_active")]
            )
            discount_info = None
            state_discount_info = None

    return PurchaseTariffs(
        cluster_name=cluster_name,
        group_code=group_code,
        tariffs=tariffs,
        discount_info=discount_info,
        state_discount_info=state_discount_info,
    )
