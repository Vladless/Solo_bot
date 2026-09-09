from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import WebPageVariant, WebPageVariantBlock


KNOWN_PAGE_SLUGS: tuple[str, ...] = (
    "landing",
    "tariffs",
    "faq",
    "login",
    "register",
    "forgot-password",
    "login-telegram-callback",
    "dashboard",
    "dashboard-keys",
    "dashboard-profile",
    "dashboard-instructions",
    "dashboard-referrals",
    "dashboard-partners",
    "dashboard-gifts",
    "dashboard-notifications",
    "checkout",
    "gift-entry",
    "referral-entry",
    "partner-entry",
    "payment-success",
    "payment-failure",
    "custom-elements",
    "info",
)

SUPPORT_BLOCK_TYPES = ("defaultSupport", "cyberMonoSupport", "capybaraSupport")
DAILY_BONUS_BLOCK_TYPES = ("defaultDailyBonus",)


class BlockLocation:
    """Где стоит блок: страница сайта и вкладка кабинета."""

    __slots__ = ("type", "slug", "tab_group", "tab_id", "data")

    def __init__(
        self,
        type: str,
        slug: str,
        tab_group: str | None,
        tab_id: str | None,
        data: dict | None = None,
    ) -> None:
        self.type = type
        self.slug = slug
        self.tab_group = tab_group
        self.tab_id = tab_id
        self.data = data or {}


async def find_block_locations(session: AsyncSession, types: list[str]) -> list[BlockLocation]:
    """Первое место для каждого из типов блоков, приоритет — активный вариант страницы."""
    wanted = [item.strip() for item in types if item and item.strip()][:20]
    if not wanted:
        return []

    result = await session.execute(
        select(
            WebPageVariantBlock.type,
            WebPageVariant.page_slug,
            WebPageVariantBlock.data,
        )
        .join(WebPageVariant, WebPageVariant.id == WebPageVariantBlock.variant_id)
        .where(WebPageVariantBlock.type.in_(wanted))
        .order_by(WebPageVariant.is_active.desc(), WebPageVariant.page_slug, WebPageVariantBlock.order)
    )

    found: dict[str, BlockLocation] = {}
    for block_type, page_slug, data in result.all():
        if block_type in found:
            continue
        payload = data if isinstance(data, dict) else {}
        tab_group = payload.get("cabinetTabGroup")
        tab_id = payload.get("cabinetTabId")
        found[block_type] = BlockLocation(
            type=block_type,
            slug=page_slug,
            tab_group=str(tab_group) if tab_group else None,
            tab_id=str(tab_id) if tab_id else None,
            data=payload,
        )
    return [found[key] for key in wanted if key in found]


def slug_to_path(slug: str) -> str:
    """Путь страницы по её slug — зеркало slugToPath в web-app/lib/web-page-registry.ts."""
    clean = (slug or "").strip()
    if not clean or clean == "landing":
        return "/"
    if clean == "dashboard":
        return "/dashboard"
    if clean.startswith("dashboard-"):
        tail = clean[len("dashboard-") :]
        return f"/dashboard/{tail}" if tail else "/dashboard"
    return f"/{clean}"


def block_location_href(location: BlockLocation, params: dict[str, str] | None = None) -> str:
    """Ссылка на страницу блока с вкладкой кабинета и своими параметрами."""
    from urllib.parse import urlencode

    path = slug_to_path(location.slug)
    query: dict[str, str] = {}
    if location.tab_id and path == "/dashboard":
        query["tab"] = location.tab_id
    for name, value in (params or {}).items():
        if value:
            query[name] = value
    return f"{path}?{urlencode(query)}" if query else path
