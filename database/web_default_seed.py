from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models.admin import Setting
from database.models.web import WebFlow, WebPage, WebPageVariant, WebPageVariantBlock
from logger import logger


DEFAULT_VARIANT_KEY = "default"
DEFAULT_VARIANT_NAME = "Основной"
PACK_DESIGN_SETTING_PREFIX = "pack_design:"


def _load_pack_site_file(pack_id: str) -> dict | None:
    """Дизайн набора берётся из установленного пака — в репозитории бота их больше нет."""
    from services.web_packs import load_pack_seed

    return load_pack_seed(pack_id)


def has_builtin_pack_file(pack_id: str) -> bool:
    return _load_pack_site_file(pack_id) is not None


DEFAULT_PACK_ID = "default"


async def _ensure_pack_seed(pack_id: str) -> dict | None:
    """Дизайн набора приезжает из источника наборов: в репозитории бота сидов нет."""
    from services.web_packs import download_and_install_pack, load_pack_seed

    seed = load_pack_seed(pack_id)
    if seed is not None:
        return seed

    result = await download_and_install_pack(pack_id)
    if not result.ok:
        logger.error("[Seed] Не удалось получить набор {}: {}", pack_id, result.error)
        return None
    return load_pack_seed(pack_id)


async def _ensure_default_pack() -> dict | None:
    return await _ensure_pack_seed(DEFAULT_PACK_ID)


def _split_site(raw: dict | None) -> tuple[dict, dict, list]:
    """Разбирает сид на (токены темы, страницы, flow)."""
    if not isinstance(raw, dict):
        return {}, {}, []
    theme = raw.get("_theme") or {}
    pages = {k: v for k, v in raw.items() if not k.startswith("_") and isinstance(v, list)}
    flows = raw.get("_flows") or []
    return theme, pages, flows


BLACK_ORANGE_THEME = {
    "primary": "#FF7A1A",
    "background": "#0A0A0A",
    "foreground": "#F2F2F2",
    "surfaceOpacity": 1,
}


def _apply_runtime_links(theme_tokens: dict) -> dict:
    try:
        from settings.config import SUPPORT_CHAT_URL, USERNAME_BOT
    except Exception:
        return theme_tokens
    footer = theme_tokens.get("footer")
    if not isinstance(footer, dict):
        return theme_tokens
    bot_url = f"https://telegram.me/{USERNAME_BOT}" if USERNAME_BOT else ""
    support_url = SUPPORT_CHAT_URL or bot_url
    links = footer.get("links")
    if isinstance(links, list):
        for link in links:
            if not isinstance(link, dict):
                continue
            label = str(link.get("label", "")).strip().lower()
            if label in ("бот", "bot") and bot_url:
                link["href"] = bot_url
            elif label in ("поддержка", "support") and support_url:
                link["href"] = support_url
    return theme_tokens


def _apply_support_links_to_pages(pages: dict) -> None:
    try:
        from settings.config import SUPPORT_CHAT_URL, USERNAME_BOT
    except Exception:
        return
    bot_url = f"https://telegram.me/{USERNAME_BOT}" if USERNAME_BOT else ""
    support_url = SUPPORT_CHAT_URL or bot_url
    if not support_url:
        return

    def walk(obj):
        if isinstance(obj, dict):
            if obj.get("telegramUsername") == "solonet_sup":
                obj["telegramUsername"] = None
                obj["linkType"] = "url"
                obj["href"] = support_url
            if obj.get("href") == "https://telegram.me/solonet_sup":
                obj["href"] = support_url
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(pages)


async def seed_default_site(session: AsyncSession, force: bool = False) -> bool:
    """Засевает дефолтный сайт. По умолчанию (force=False) — только в пустую БД
    (идемпотентно). При force=True перезаписывает страницы дефолта (кнопка
    «Установить дефолтный дизайн»). Возвращает True, если что-то записано."""
    if not force:
        existing = await session.execute(select(func.count(WebPageVariantBlock.id)))
        if (existing.scalar() or 0) > 0:
            return False

    theme, pages, flows = _split_site(await _ensure_default_pack())
    if not pages:
        return False
    theme_tokens = theme or BLACK_ORANGE_THEME
    theme_tokens = _apply_runtime_links(theme_tokens)
    return await _apply_site(session, theme_tokens, pages, flows, force, page_themes=None)


async def _apply_site(
    session: AsyncSession,
    theme_tokens: dict,
    pages: dict,
    flows: list,
    force: bool,
    page_themes: dict | None = None,
    global_theme: dict | None = None,
) -> bool:
    """Применяет распакованный сайт (тема, страницы, flow) к БД.
    page_themes[slug] (если задан) переопределяет тему конкретной страницы — нужно
    для захваченных наборов, где у кабинета свои page-scoped токены.
    global_theme — палитра, которую набор доносит до остальных страниц сайта
    (лендинг и т.д.), не трогая их блоки."""
    page_themes = page_themes or {}
    _apply_support_links_to_pages(pages)
    seeded = False
    for slug, blocks in pages.items():
        page_theme = page_themes.get(slug)
        page_theme_tokens = dict(page_theme) if isinstance(page_theme, dict) else dict(theme_tokens)
        page = (await session.execute(select(WebPage).where(WebPage.slug == slug))).scalar_one_or_none()
        if page is None:
            session.add(WebPage(slug=slug, title=slug))
            await session.flush()

        variant = (
            await session.execute(
                select(WebPageVariant).where(
                    WebPageVariant.page_slug == slug,
                    WebPageVariant.variant_key == DEFAULT_VARIANT_KEY,
                )
            )
        ).scalar_one_or_none()
        if variant is None:
            variant = WebPageVariant(
                page_slug=slug,
                variant_key=DEFAULT_VARIANT_KEY,
                name=DEFAULT_VARIANT_NAME,
                is_active=True,
                theme_tokens=dict(page_theme_tokens),
            )
            session.add(variant)
            await session.flush()
        else:
            variant.is_active = True
            variant.theme_tokens = dict(page_theme_tokens)
            if force:
                await session.execute(delete(WebPageVariantBlock).where(WebPageVariantBlock.variant_id == variant.id))

        if force:
            await session.execute(
                update(WebPageVariant)
                .where(
                    WebPageVariant.page_slug == slug,
                    WebPageVariant.variant_key != DEFAULT_VARIANT_KEY,
                )
                .values(is_active=False)
            )

        for order, block in enumerate(blocks):
            session.add(
                WebPageVariantBlock(
                    variant_id=variant.id,
                    order=order,
                    type=block["type"],
                    data=dict(block["data"]),
                )
            )
        seeded = True

    if global_theme:
        rest = (
            (
                await session.execute(
                    select(WebPageVariant).where(
                        WebPageVariant.page_slug.notin_(list(pages.keys()) or [""]),
                        WebPageVariant.variant_key == DEFAULT_VARIANT_KEY,
                    )
                )
            )
            .scalars()
            .all()
        )
        for variant in rest:
            variant.theme_tokens = {**dict(variant.theme_tokens or {}), **global_theme}
            seeded = True

    for flow in flows:
        if not isinstance(flow, dict):
            continue
        fid = str(flow.get("id") or "").strip()
        if not fid:
            continue
        nodes = list(flow.get("nodes") or [])
        edges = list(flow.get("edges") or [])
        name = flow.get("name") or fid
        entry = flow.get("entry_node_id")
        existing = await session.get(WebFlow, fid)
        if existing is None:
            session.add(WebFlow(id=fid, name=name, nodes=nodes, edges=edges, entry_node_id=entry, version=1))
            seeded = True
        elif force:
            existing.name = name
            existing.nodes = nodes
            existing.edges = edges
            existing.entry_node_id = entry
            existing.version = (existing.version or 0) + 1
            seeded = True

    await session.flush()
    return seeded


async def capture_current_site(session: AsyncSession) -> dict:
    """Снимок текущего сайта в формате seed: {_theme, _flows, _page_themes, <slug>: [{type,data}]}.
    Тему берём по каждой странице отдельно (_page_themes), глобальную (_theme) — со страницы landing."""
    out: dict = {}
    page_themes: dict = {}
    global_theme: dict | None = None
    pages = (await session.execute(select(WebPage))).scalars().all()
    for page in pages:
        variant = (
            await session.execute(
                select(WebPageVariant).where(
                    WebPageVariant.page_slug == page.slug,
                    WebPageVariant.variant_key == DEFAULT_VARIANT_KEY,
                )
            )
        ).scalar_one_or_none()
        if variant is None:
            continue
        tokens = dict(variant.theme_tokens or {})
        page_themes[page.slug] = tokens
        if page.slug == "landing":
            global_theme = tokens
        blocks = (
            (
                await session.execute(
                    select(WebPageVariantBlock)
                    .where(WebPageVariantBlock.variant_id == variant.id)
                    .order_by(WebPageVariantBlock.order.asc())
                )
            )
            .scalars()
            .all()
        )
        out[page.slug] = [{"type": b.type, "data": dict(b.data or {})} for b in blocks]
    out["_theme"] = global_theme or (next(iter(page_themes.values()), {}) if page_themes else {})
    out["_page_themes"] = page_themes
    flows = (await session.execute(select(WebFlow))).scalars().all()
    out["_flows"] = [
        {
            "id": f.id,
            "name": f.name,
            "nodes": list(f.nodes or []),
            "edges": list(f.edges or []),
            "entry_node_id": f.entry_node_id,
        }
        for f in flows
    ]
    return out


async def store_pack_design(session: AsyncSession, pack_id: str, site: dict, meta: dict | None = None) -> None:
    payload = dict(site)
    if meta is not None:
        payload["_meta"] = meta
    key = f"{PACK_DESIGN_SETTING_PREFIX}{pack_id}"
    setting = (await session.execute(select(Setting).where(Setting.key == key))).scalar_one_or_none()
    if setting is None:
        session.add(Setting(key=key, value=payload, description=f"Сохранённый дизайн набора {pack_id}"))
    else:
        setting.value = payload
    await session.flush()


async def delete_pack_design(session: AsyncSession, pack_id: str) -> bool:
    key = f"{PACK_DESIGN_SETTING_PREFIX}{pack_id}"
    setting = (await session.execute(select(Setting).where(Setting.key == key))).scalar_one_or_none()
    if setting is None:
        return False
    await session.delete(setting)
    await session.flush()
    return True


async def list_custom_pack_designs(session: AsyncSession, builtin_ids: set[str]) -> list[dict]:
    """Список своих (не встроенных) сохранённых наборов: [{id, name, description}]."""
    rows = (
        await session.execute(
            select(Setting.key, Setting.value).where(Setting.key.like(f"{PACK_DESIGN_SETTING_PREFIX}%"))
        )
    ).all()
    out: list[dict] = []
    for key, value in rows:
        pack_id = str(key)[len(PACK_DESIGN_SETTING_PREFIX) :]
        if not pack_id or pack_id in builtin_ids:
            continue
        meta = value.get("_meta") if isinstance(value, dict) else None
        meta = meta if isinstance(meta, dict) else {}
        out.append({
            "id": pack_id,
            "name": str(meta.get("name") or pack_id),
            "description": str(meta.get("description") or ""),
        })
    out.sort(key=lambda p: p["name"].lower())
    return out


async def load_pack_design(session: AsyncSession, pack_id: str) -> dict | None:
    key = f"{PACK_DESIGN_SETTING_PREFIX}{pack_id}"
    setting = (await session.execute(select(Setting).where(Setting.key == key))).scalar_one_or_none()
    value = setting.value if setting is not None else None
    return value if isinstance(value, dict) else None


async def capture_and_store_pack_design(
    session: AsyncSession, pack_id: str, name: str | None = None, description: str | None = None
) -> dict:
    site = await capture_current_site(session)
    meta = None
    if name is not None or description is not None:
        meta = {"name": (name or pack_id), "description": (description or ""), "custom": True}
    await store_pack_design(session, pack_id, site, meta=meta)
    return site


async def _apply_captured_site(session: AsyncSession, site: dict) -> bool:
    pages = {k: v for k, v in site.items() if not k.startswith("_") and isinstance(v, list)}
    theme = _apply_runtime_links(dict(site.get("_theme") or {}))
    page_themes = site.get("_page_themes") if isinstance(site.get("_page_themes"), dict) else {}
    flows = site.get("_flows") or []
    raw_global = site.get("_global_theme")
    global_theme = dict(raw_global) if isinstance(raw_global, dict) else None
    return await _apply_site(
        session, theme, pages, flows, force=True, page_themes=page_themes, global_theme=global_theme
    )


async def install_pack_design(session: AsyncSession, pack_id: str) -> bool:
    """Устанавливает дизайн набора (страницы + темы + flow). force=True.
    Приоритет: сохранённый в БД дизайн → сид набора, при нужде скачанный с сайта."""
    site = await load_pack_design(session, pack_id)
    if not site:
        site = await _ensure_pack_seed(pack_id)
    if not site:
        return False
    return await _apply_captured_site(session, site)
