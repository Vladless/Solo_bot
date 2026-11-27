import re

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import WebBlock, WebPageVariant, WebPageVariantBlock, WebTheme
from database.news import validate_news_link_url


def _identifier(value: object) -> str:
    """Читает короткий идентификатор вкладки без управляющих символов."""
    return (
        value.strip()
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value.strip())
        else ""
    )


def news_cabinet_href(tab_id: str) -> str:
    """Собирает адрес вкладки основного кабинета."""
    tab_id = _identifier(tab_id)
    if not tab_id:
        raise ValueError("Укажите вкладку кабинета")
    return f"/dashboard?{urlencode({'cabinetTab': tab_id})}"


def collect_news_cabinet_targets(tokens: object, blocks: list[object]) -> list[dict[str, str]]:
    """Собирает настроенные вкладки, сохраняя их реальные адреса и порядок."""
    raw = tokens.get("cabinetNavItems") if isinstance(tokens, dict) else None
    configured = isinstance(raw, list) and bool(raw)
    if not configured:
        raw = []
        for block in blocks:
            payload = block.data if hasattr(block, "data") else block.get("data", {}) if isinstance(block, dict) else {}
            payload = payload if isinstance(payload, dict) else {}
            block_type = (
                block.type if hasattr(block, "type") else block.get("type", "") if isinstance(block, dict) else ""
            )
            group = payload.get("tabGroupId") or payload.get("tabsGroup")
            if block_type == "cabinetTabBar" or group == "cabinet":
                items = payload.get("items") or payload.get("tabs")
                if isinstance(items, list):
                    raw.extend(items)
        if not raw:
            for block in blocks:
                payload = (
                    block.data if hasattr(block, "data") else block.get("data", {}) if isinstance(block, dict) else {}
                )
                if isinstance(payload, dict) and payload.get("cabinetTabGroup", "cabinet") == "cabinet":
                    tab_id = _identifier(payload.get("cabinetTabId"))
                    if tab_id:
                        raw.append({"id": tab_id, "label": payload.get("cabinetTabLabel") or tab_id})
    found: dict[str, dict[str, str]] = {}
    indexed = [(index, item) for index, item in enumerate(raw) if isinstance(item, dict)]
    indexed.sort(key=lambda pair: pair[1].get("order") if isinstance(pair[1].get("order"), int | float) else pair[0])
    for _, item in indexed:
        tab_id = _identifier(item.get("id"))
        if (
            not tab_id
            or tab_id == "logout"
            or item.get("action") == "logout"
            or item.get("visible") is False
            or tab_id in found
        ):
            continue
        href = item.get("slug") or item.get("href") or news_cabinet_href(tab_id)
        try:
            href = validate_news_link_url(href)
        except ValueError:
            continue
        if not href or not (href.split("?", 1)[0] == "/dashboard" or href.startswith("/dashboard/")):
            continue
        parsed = urlsplit(href)
        if parsed.path == "/dashboard":
            query = dict(parse_qsl(parsed.query, keep_blank_values=True))
            if not query.get("cabinetTab") and not query.get("tab"):
                query["cabinetTab"] = tab_id
            href = urlunsplit(("", "", parsed.path, urlencode(query), parsed.fragment))
        label = item.get("label")
        found[tab_id] = {
            "id": tab_id,
            "label": label.strip()[:120] if isinstance(label, str) and label.strip() else tab_id,
            "href": href,
        }
    return list(found.values())


async def list_news_cabinet_targets(session: AsyncSession) -> list[dict[str, str]]:
    """Читает вкладки активного кабинета без создания страниц или вариантов."""
    variant = await session.scalar(
        select(WebPageVariant)
        .where(WebPageVariant.page_slug == "dashboard", WebPageVariant.is_active.is_(True))
        .order_by(WebPageVariant.created_at.desc(), WebPageVariant.id)
        .limit(1)
    )
    if variant is not None:
        blocks = list(
            (
                await session.scalars(
                    select(WebPageVariantBlock)
                    .where(WebPageVariantBlock.variant_id == variant.id)
                    .order_by(WebPageVariantBlock.order)
                )
            ).all()
        )
        return collect_news_cabinet_targets(variant.theme_tokens, blocks)
    theme = await session.get(WebTheme, "dashboard")
    blocks = list(
        (
            await session.scalars(select(WebBlock).where(WebBlock.page_slug == "dashboard").order_by(WebBlock.order))
        ).all()
    )
    return collect_news_cabinet_targets(theme.tokens if theme is not None else {}, blocks)
