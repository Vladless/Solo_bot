import hashlib
import re
import uuid

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import _identity_from_cookie, get_session, verify_identity_designer
from api.v2.routes._data_uri_migration import migrate_json_data_uris
from api.v2.schemas import WebBlockResponse, WebPageResponse, WebPageUpdate, WebTheme
from api.v2.schemas.web import (
    WebPageSaveResponse,
    WebPageThemeResponse,
    WebPageThemeUpdate,
    WebPageVariantCreate,
    WebPageVariantSummary,
    WebPageVariantUpdate,
    WebPageVariantsResponse,
    WebUploadResponse,
)
from database.models import (
    WebBlock,
    WebCustomElementBuild,
    WebErrorReport,
    WebFlow,
    WebFlowEvent,
    WebPage,
    WebPageVariant,
    WebPageVariantBlock,
    WebPageView,
    WebTheme as WebThemeModel,
)
from database.site_revision import bump_site_revision
from logger import logger


UPLOAD_DIR = Path("static/web_uploads")
ALLOWED_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".mp4", ".webm"})
MAX_FILE_SIZE = 100 * 1024 * 1024
_IMAGE_RESIZE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})
_IMAGE_MAX_SIDE = 2048
_IMAGE_JPEG_QUALITY = 85
_IMAGE_WEBP_QUALITY = 85

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]*$")

EXTENSION_CONTENT_TYPES: dict[str, frozenset[str]] = {
    ".png": frozenset({"image/png"}),
    ".jpg": frozenset({"image/jpeg"}),
    ".jpeg": frozenset({"image/jpeg"}),
    ".gif": frozenset({"image/gif"}),
    ".webp": frozenset({"image/webp"}),
    ".svg": frozenset({"image/svg+xml", "text/xml", "application/xml", "text/plain"}),
    ".mp4": frozenset({"video/mp4"}),
    ".webm": frozenset({"video/webm"}),
}


def _optimize_image_bytes(data: bytes, ext: str) -> bytes:
    """Уменьшает большие картинки до _IMAGE_MAX_SIDE и пережимает с разумным качеством."""
    try:
        from io import BytesIO

        from PIL import Image, ImageOps

        with Image.open(BytesIO(data)) as img:
            img = ImageOps.exif_transpose(img)
            w, h = img.size
            if max(w, h) <= _IMAGE_MAX_SIDE and len(data) < 500_000:
                return data
            if max(w, h) > _IMAGE_MAX_SIDE:
                img.thumbnail((_IMAGE_MAX_SIDE, _IMAGE_MAX_SIDE), Image.Resampling.LANCZOS)
            buffer = BytesIO()
            save_kwargs: dict = {}
            if ext in (".jpg", ".jpeg"):
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                save_kwargs = {"format": "JPEG", "quality": _IMAGE_JPEG_QUALITY, "optimize": True, "progressive": True}
            elif ext == ".webp":
                save_kwargs = {"format": "WEBP", "quality": _IMAGE_WEBP_QUALITY, "method": 6}
            elif ext == ".png":
                save_kwargs = {"format": "PNG", "optimize": True}
            else:
                return data
            img.save(buffer, **save_kwargs)
            optimized = buffer.getvalue()
            return optimized if len(optimized) < len(data) else data
    except Exception:
        return data


_SVG_NS = "http://www.w3.org/2000/svg"
_XLINK_NS = "http://www.w3.org/1999/xlink"
_SVG_ALLOWED_TAGS = frozenset({
    "svg",
    "g",
    "path",
    "rect",
    "circle",
    "ellipse",
    "line",
    "polyline",
    "polygon",
    "text",
    "tspan",
    "textpath",
    "defs",
    "lineargradient",
    "radialgradient",
    "stop",
    "clippath",
    "mask",
    "pattern",
    "use",
    "symbol",
    "title",
    "desc",
    "marker",
    "metadata",
    "filter",
    "fegaussianblur",
    "feoffset",
    "feblend",
    "femerge",
    "femergenode",
    "fecolormatrix",
    "fecomposite",
    "feflood",
    "femorphology",
    "fedropshadow",
    "fespecularlighting",
    "fediffuselighting",
    "fepointlight",
    "fedistantlight",
    "fetile",
    "feturbulence",
    "fedisplacementmap",
    "fecomponenttransfer",
    "fefuncr",
    "fefuncg",
    "fefuncb",
    "fefunca",
    "switch",
})
_SVG_BAD_URL_SCHEMES = ("javascript:", "vbscript:", "data:text/html")


def _svg_local(name: str) -> str:
    return str(name).rsplit("}", 1)[-1].lower()


def _scrub_svg_element(elem) -> None:
    for attr in list(elem.attrib.keys()):
        local = _svg_local(attr)
        value = elem.attrib.get(attr) or ""
        if local.startswith("on"):
            del elem.attrib[attr]
            continue
        if local == "href":
            clean = re.sub(r"\s", "", value).lower()
            if any(clean.startswith(scheme.replace(" ", "")) for scheme in _SVG_BAD_URL_SCHEMES):
                del elem.attrib[attr]
                continue
        if local == "style":
            low = value.lower()
            if "javascript:" in low or "expression(" in low or "@import" in low or "behavior:" in low:
                del elem.attrib[attr]
    for child in list(elem):
        if _svg_local(child.tag) not in _SVG_ALLOWED_TAGS:
            elem.remove(child)
        else:
            _scrub_svg_element(child)


def _sanitize_svg_xml(data: bytes) -> bytes:
    import xml.etree.ElementTree as ET

    from defusedxml.ElementTree import fromstring

    ET.register_namespace("", _SVG_NS)
    ET.register_namespace("xlink", _XLINK_NS)
    root = fromstring(data)
    if _svg_local(root.tag) != "svg":
        raise ValueError("not an svg root")
    _scrub_svg_element(root)
    return ET.tostring(root, encoding="utf-8")


def _sanitize_svg_regex(data: bytes) -> bytes:
    text = data.decode("utf-8", errors="replace")
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"\bon\w+\s*=\s*[\"'][^\"']*[\"']", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bon\w+\s*=\s*\S+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(?:href|xlink:href)\s*=\s*[\"']\s*javascript:[^\"']*[\"']", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(?:href|xlink:href)\s*=\s*[\"']\s*data:\s*text/html[^\"']*[\"']", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(?:href|xlink:href)\s*=\s*[\"']\s*vbscript:[^\"']*[\"']", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<foreignObject[^>]*>.*?</foreignObject>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<iframe[^>]*>.*?</iframe>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<embed[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<object[^>]*>.*?</object>", "", text, flags=re.DOTALL | re.IGNORECASE)
    return text.encode("utf-8")


def _sanitize_svg(data: bytes) -> bytes:
    try:
        return _sanitize_svg_xml(data)
    except Exception:
        return _sanitize_svg_regex(data)


router = APIRouter(tags=["Web"])


async def _audit_web_admin(
    session: AsyncSession,
    identity,
    action: str,
    *,
    entity_type: str | None = None,
    entity_id: str | int | None = None,
    metadata: dict | None = None,
) -> None:
    """Пишет действие админа над сайтом в журнал аудита (event_type=web_admin_action)."""
    try:
        from audit import safe_record_audit_event

        await safe_record_audit_event(
            session,
            event_type="web_admin_action",
            channel="api",
            path_or_handler=action,
            actor_identity_id=getattr(identity, "id", None),
            actor_tg_id=getattr(identity, "tg_id", None),
            entity_type=entity_type,
            entity_id=entity_id,
            metadata=metadata,
        )
    except Exception:
        pass


class WebPagesListResponse(BaseModel):
    slugs: list[str]


class WebPageCreateBody(BaseModel):
    slug: str
    title: str | None = None


CORE_PAGE_SLUGS = frozenset({
    "landing",
    "dashboard",
    "login",
    "checkout",
    "tariffs",
})


KNOWN_PAGE_SLUGS = [
    "landing",
    "tariffs",
    "faq",
    "login",
    "dashboard",
    "checkout",
    "gift-entry",
    "referral-entry",
    "partner-entry",
    "payment-success",
    "payment-failure",
    "dashboard-keys",
    "dashboard-profile",
    "dashboard-instructions",
    "dashboard-referrals",
]

DEFAULT_VARIANT_KEY = "default"
DEFAULT_VARIANT_NAME = "Основной"


def _normalize_variant_key(value: str | None) -> str:
    raw = (value or "").strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    if not normalized:
        return DEFAULT_VARIANT_KEY
    return normalized[:64].strip("-") or DEFAULT_VARIANT_KEY


def _normalize_variant_name(value: str | None, fallback: str) -> str:
    name = (value or "").strip()
    return name[:255] if name else fallback


def _variant_summary(row: WebPageVariant) -> WebPageVariantSummary:
    return WebPageVariantSummary(
        key=row.variant_key,
        name=row.name or row.variant_key,
        is_active=bool(row.is_active),
    )


@router.get("/api/web/pages", response_model=WebPagesListResponse)
async def list_web_pages(
    session: AsyncSession = Depends(get_session),
):
    """Список slug всех страниц сайта (для экспорта/импорта). Включает известные страницы, даже если запись ещё не создана."""
    result = await session.execute(select(WebPage.slug).order_by(WebPage.slug))
    from_db = {row[0] for row in result.fetchall()}
    slugs = sorted(from_db | set(KNOWN_PAGE_SLUGS))
    return WebPagesListResponse(slugs=slugs)


@router.post("/api/web/pages", response_model=WebPagesListResponse)
async def create_web_page(
    body: WebPageCreateBody,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_designer),
):
    slug = (body.slug or "").strip().lower()
    if not _SLUG_RE.match(slug) or len(slug) > 64:
        raise HTTPException(status_code=400, detail="invalid_slug")
    existing = await session.execute(select(WebPage).where(WebPage.slug == slug))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="slug_already_exists")
    title = (body.title or slug).strip()[:255] or slug
    page = WebPage(slug=slug, title=title)
    session.add(page)
    await session.flush()
    variant = WebPageVariant(
        page_slug=slug,
        variant_key=DEFAULT_VARIANT_KEY,
        name=DEFAULT_VARIANT_NAME,
        is_active=True,
        theme_tokens={},
    )
    session.add(variant)
    await session.flush()
    await bump_site_revision(session)
    await _audit_web_admin(session, identity, "page.create", entity_type="page", entity_id=slug)
    result = await session.execute(select(WebPage.slug).order_by(WebPage.slug))
    from_db = {row[0] for row in result.fetchall()}
    slugs = sorted(from_db | set(KNOWN_PAGE_SLUGS))
    return WebPagesListResponse(slugs=slugs)


@router.delete("/api/web/pages/{slug}", response_model=WebPagesListResponse)
async def delete_web_page(
    slug: str,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_designer),
):
    if slug in CORE_PAGE_SLUGS:
        raise HTTPException(status_code=403, detail="core_page_protected")
    page_q = await session.execute(select(WebPage).where(WebPage.slug == slug))
    page = page_q.scalar_one_or_none()
    if page is None:
        raise HTTPException(status_code=404, detail="page_not_found")
    variants_q = await session.execute(select(WebPageVariant.id).where(WebPageVariant.page_slug == slug))
    variant_ids = [row[0] for row in variants_q.fetchall()]
    if variant_ids:
        await session.execute(delete(WebPageVariantBlock).where(WebPageVariantBlock.variant_id.in_(variant_ids)))
        await session.execute(delete(WebPageVariant).where(WebPageVariant.page_slug == slug))
    await session.execute(delete(WebBlock).where(WebBlock.page_slug == slug))
    await session.execute(delete(WebThemeModel).where(WebThemeModel.page_slug == slug))
    await session.delete(page)

    flows_q = await session.execute(select(WebFlow))
    for flow in flows_q.scalars().all():
        nodes = flow.nodes
        if not isinstance(nodes, list):
            continue
        changed = False
        new_nodes = []
        for node in nodes:
            if isinstance(node, dict) and (node.get("page_slug") == slug or node.get("pageSlug") == slug):
                node = dict(node)
                if node.get("page_slug") == slug:
                    node["page_slug"] = None
                if node.get("pageSlug") == slug:
                    node["pageSlug"] = None
                changed = True
            new_nodes.append(node)
        if changed:
            flow.nodes = new_nodes

    await session.flush()
    await bump_site_revision(session)
    await _audit_web_admin(session, identity, "page.delete", entity_type="page", entity_id=slug)
    result = await session.execute(select(WebPage.slug).order_by(WebPage.slug))
    from_db = {row[0] for row in result.fetchall()}
    slugs = sorted(from_db | set(KNOWN_PAGE_SLUGS))
    return WebPagesListResponse(slugs=slugs)


async def get_or_create_page(session: AsyncSession, slug: str) -> WebPage:
    result = await session.execute(select(WebPage).where(WebPage.slug == slug))
    page = result.scalar_one_or_none()
    if page is None:
        if slug not in KNOWN_PAGE_SLUGS:
            raise HTTPException(status_code=404, detail="page_not_found")
        page = WebPage(slug=slug, title=slug)
        session.add(page)
        await session.flush()
    return page


async def _list_variants(session: AsyncSession, slug: str) -> list[WebPageVariant]:
    result = await session.execute(
        select(WebPageVariant)
        .where(WebPageVariant.page_slug == slug)
        .order_by(WebPageVariant.is_active.desc(), WebPageVariant.created_at, WebPageVariant.variant_key)
    )
    return list(result.scalars().all())


async def _get_theme_tokens_for_legacy_page(session: AsyncSession, slug: str) -> dict:
    theme_result = await session.execute(select(WebThemeModel).where(WebThemeModel.page_slug == slug))
    theme_row = theme_result.scalar_one_or_none()
    return dict(theme_row.tokens or {}) if theme_row else {}


async def _get_legacy_blocks(session: AsyncSession, slug: str) -> list[WebBlock]:
    blocks_result = await session.execute(
        select(WebBlock).where(WebBlock.page_slug == slug).order_by(WebBlock.order, WebBlock.id)
    )
    return list(blocks_result.scalars().all())


async def _ensure_page_variants(session: AsyncSession, slug: str) -> list[WebPageVariant]:
    await get_or_create_page(session, slug)
    variants = await _list_variants(session, slug)
    if variants:
        if not any(variant.is_active for variant in variants):
            variants[0].is_active = True
            await session.flush()
            variants = await _list_variants(session, slug)
        return variants

    legacy_blocks = await _get_legacy_blocks(session, slug)
    theme_tokens = await _get_theme_tokens_for_legacy_page(session, slug)
    variant = WebPageVariant(
        page_slug=slug,
        variant_key=DEFAULT_VARIANT_KEY,
        name=DEFAULT_VARIANT_NAME,
        is_active=True,
        theme_tokens=theme_tokens,
    )
    session.add(variant)
    await session.flush()
    for legacy_block in legacy_blocks:
        session.add(
            WebPageVariantBlock(
                variant_id=variant.id,
                order=legacy_block.order,
                type=legacy_block.type,
                data=legacy_block.data,
            )
        )
    await session.flush()
    return await _list_variants(session, slug)


async def _resolve_variant(
    session: AsyncSession,
    slug: str,
    variant_key: str | None,
    ab_bucket: str | None = None,
) -> tuple[WebPageVariant, list[WebPageVariant]]:
    variants = await _ensure_page_variants(session, slug)
    desired_key = _normalize_variant_key(variant_key) if variant_key else ""
    if desired_key:
        current = next((variant for variant in variants if variant.variant_key == desired_key), None)
        if current is None:
            raise HTTPException(404, "Вариант страницы не найден")
        return current, variants
    active = next((variant for variant in variants if variant.is_active), variants[0])
    active_variants = [variant for variant in variants if variant.is_active]
    bucket = (ab_bucket or "").strip().lower()
    if bucket and len(bucket) == 1 and bucket.isalpha() and len(active_variants) > 1:
        idx = ord(bucket) - ord("a")
        return active_variants[idx % len(active_variants)], variants
    return active, variants


async def _get_variant_blocks(session: AsyncSession, variant_id: str) -> list[WebBlockResponse]:
    blocks_result = await session.execute(
        select(WebPageVariantBlock)
        .where(WebPageVariantBlock.variant_id == variant_id)
        .order_by(WebPageVariantBlock.order, WebPageVariantBlock.id)
    )
    return [WebBlockResponse.model_validate(block) for block in blocks_result.scalars().all()]


async def _build_page_response(
    session: AsyncSession,
    slug: str,
    current: WebPageVariant,
    variants: list[WebPageVariant] | None = None,
) -> WebPageResponse:
    current_variants = variants or await _list_variants(session, slug)
    active = next((variant for variant in current_variants if variant.is_active), current)
    blocks = await _get_variant_blocks(session, current.id)
    theme = WebTheme(tokens=dict(current.theme_tokens or {}))
    return WebPageResponse(
        slug=slug,
        blocks=blocks,
        theme=theme,
        variant_key=current.variant_key,
        active_variant_key=active.variant_key,
        variants=[_variant_summary(variant) for variant in current_variants],
    )


async def _set_active_variant(session: AsyncSession, slug: str, variant_key: str) -> list[WebPageVariant]:
    variants = await _list_variants(session, slug)
    matched = False
    for variant in variants:
        is_target = variant.variant_key == variant_key
        variant.is_active = is_target
        matched = matched or is_target
    if not matched:
        raise HTTPException(404, "Вариант страницы не найден")
    await session.flush()
    return await _list_variants(session, slug)


def _generate_variant_key(existing_keys: set[str], requested_key: str | None, requested_name: str | None) -> str:
    base = _normalize_variant_key(requested_key or requested_name)
    if not base:
        base = DEFAULT_VARIANT_KEY
    if base not in existing_keys:
        return base
    suffix = 2
    while True:
        candidate = f"{base}-{suffix}"
        if candidate not in existing_keys:
            return candidate[:64]
        suffix += 1


@router.get("/api/web/pages/{slug}", response_model=WebPageResponse)
async def get_web_page(
    slug: str,
    variant: str | None = Query(default=None),
    ab: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    if not slug or len(slug) > 64 or not _SLUG_RE.match(slug):
        raise HTTPException(400, "Некорректный slug страницы")
    current, variants = await _resolve_variant(session, slug, variant, ab_bucket=ab)
    return await _build_page_response(session, slug, current, variants)


@router.get("/api/web/pages/{slug}/theme", response_model=WebPageThemeResponse)
async def get_web_page_theme(
    slug: str,
    variant: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    """Возвращает только theme tokens страницы (без блоков/вариантов). Используется для fallback-темы cabinet-страниц."""
    if not slug or len(slug) > 64 or not _SLUG_RE.match(slug):
        raise HTTPException(400, "Некорректный slug страницы")
    current, _ = await _resolve_variant(session, slug, variant)
    return WebPageThemeResponse(
        slug=slug,
        variant_key=current.variant_key,
        tokens=dict(current.theme_tokens or {}),
    )


@router.put("/api/web/pages/{slug}/theme", response_model=WebPageThemeResponse)
async def update_web_page_theme(
    slug: str,
    body: WebPageThemeUpdate,
    variant: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_designer),
):
    """Обновляет только theme_tokens страницы (без трогания блоков). Используется для sync темы между страницами."""
    if not slug or len(slug) > 64 or not _SLUG_RE.match(slug):
        raise HTTPException(400, "Некорректный slug страницы")
    current, _ = await _resolve_variant(session, slug, variant)
    cleaned_tokens, replaced = migrate_json_data_uris(body.tokens)
    if replaced:
        logger.info("[web] theme PUT slug={} replaced {} data: URI(s)", slug, replaced)
    current.theme_tokens = cleaned_tokens
    await session.flush()
    await bump_site_revision(session)
    await _audit_web_admin(
        session,
        identity,
        "page.theme.update",
        entity_type="page",
        entity_id=slug,
        metadata={"variant": current.variant_key},
    )
    return WebPageThemeResponse(
        slug=slug,
        variant_key=current.variant_key,
        tokens=dict(current.theme_tokens or {}),
    )


class WebPageTitleUpdate(BaseModel):
    title: str | None = None


@router.get("/api/web/page-titles")
async def list_web_page_titles(
    session: AsyncSession = Depends(get_session),
    _identity=Depends(verify_identity_designer),
):
    """Кастомные заголовки вкладки (токен pageTitle) по всем страницам — для админ-списка «Страницы»."""
    rows = (
        await session.execute(
            select(WebPageVariant.page_slug, WebPageVariant.theme_tokens).where(WebPageVariant.is_active.is_(True))
        )
    ).all()
    titles: dict[str, str] = {}
    for page_slug, tokens in rows:
        value = (tokens or {}).get("pageTitle")
        if isinstance(value, str) and value.strip():
            titles[page_slug] = value.strip()
    return {"titles": titles}


@router.put("/api/web/pages/{slug}/title")
async def update_web_page_title(
    slug: str,
    body: WebPageTitleUpdate,
    variant: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_designer),
):
    """Кастомный заголовок вкладки браузера для страницы (токен pageTitle). Пусто — сброс к дефолту."""
    if not slug or len(slug) > 64 or not _SLUG_RE.match(slug):
        raise HTTPException(400, "Некорректный slug страницы")
    current, _ = await _resolve_variant(session, slug, variant)
    tokens = dict(current.theme_tokens or {})
    title = (body.title or "").strip()[:255]
    if title:
        tokens["pageTitle"] = title
    else:
        tokens.pop("pageTitle", None)
    current.theme_tokens = tokens
    await session.flush()
    await bump_site_revision(session)
    await _audit_web_admin(
        session,
        identity,
        "page.title.update",
        entity_type="page",
        entity_id=slug,
        metadata={"variant": current.variant_key, "set": bool(title)},
    )
    return {"slug": slug, "title": title or None}


class PwaIconUpdate(BaseModel):
    url: str | None = None


def _valid_pwa_icon_url(raw: str | None) -> str:
    url = (raw or "").strip()
    if not url:
        return ""
    prefix = "/api/web/uploads/"
    if not url.startswith(prefix) or len(url) > 255 or ".." in url or "\\" in url:
        raise HTTPException(400, "Недопустимый адрес иконки")
    filename = url[len(prefix) :]
    if not re.fullmatch(r"[A-Za-z0-9._-]+", filename):
        raise HTTPException(400, "Недопустимый адрес иконки")
    return url


@router.get("/api/web/pwa-icon")
async def get_pwa_icon(session: AsyncSession = Depends(get_session)):
    current, _ = await _resolve_variant(session, "landing", None)
    tokens = dict(current.theme_tokens or {})
    url = tokens.get("pwaIconUrl")
    return {"url": url if isinstance(url, str) and url.strip() else None}


@router.put("/api/web/pwa-icon")
async def set_pwa_icon(
    body: PwaIconUpdate,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_designer),
):
    url = _valid_pwa_icon_url(body.url)
    current, _ = await _resolve_variant(session, "landing", None)
    tokens = dict(current.theme_tokens or {})
    if url:
        tokens["pwaIconUrl"] = url
    else:
        tokens.pop("pwaIconUrl", None)
    current.theme_tokens = tokens
    await session.flush()
    await bump_site_revision(session)
    await _audit_web_admin(
        session, identity, "pwa_icon.set", entity_type="setting", entity_id="pwaIconUrl", metadata={"set": bool(url)}
    )
    return {"url": url or None}


@router.put("/api/web/pages/{slug}")
async def update_web_page(
    slug: str,
    body: WebPageUpdate,
    variant: str | None = Query(default=None),
    minimal: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_designer),
):
    current, _ = await _resolve_variant(session, slug, variant)
    await session.execute(delete(WebPageVariantBlock).where(WebPageVariantBlock.variant_id == current.id))

    total_replaced = 0
    for block in body.blocks:
        cleaned_data, replaced = migrate_json_data_uris(block.data)
        total_replaced += replaced
        session.add(
            WebPageVariantBlock(
                variant_id=current.id,
                order=block.order,
                type=block.type,
                data=cleaned_data,
            )
        )

    if body.theme is not None:
        cleaned_theme, theme_replaced = migrate_json_data_uris(body.theme.tokens)
        total_replaced += theme_replaced
        current.theme_tokens = cleaned_theme

    if total_replaced:
        logger.info("[web] page PUT slug={} replaced {} data: URI(s)", slug, total_replaced)

    await session.flush()
    await bump_site_revision(session)
    await _audit_web_admin(
        session,
        identity,
        "page.update",
        entity_type="page",
        entity_id=slug,
        metadata={"variant": current.variant_key, "blocks": len(body.blocks)},
    )
    refreshed_variants = await _list_variants(session, slug)
    refreshed_current = next((item for item in refreshed_variants if item.id == current.id), current)
    if minimal:
        active = next((item for item in refreshed_variants if item.is_active), refreshed_current)
        return WebPageSaveResponse(
            slug=slug,
            variant_key=refreshed_current.variant_key,
            active_variant_key=active.variant_key,
            variants=[_variant_summary(item) for item in refreshed_variants],
        )
    return await _build_page_response(session, slug, refreshed_current, refreshed_variants)


@router.get("/api/web/pages/{slug}/variants", response_model=WebPageVariantsResponse)
async def get_web_page_variants(
    slug: str,
    variant: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    try:
        current, variants = await _resolve_variant(session, slug, variant)
    except HTTPException:
        raise
    except Exception as exc:
        from logger import logger

        logger.warning("[web] variants resolve failed for slug={}: {}", slug, exc)
        raise HTTPException(status_code=404, detail="Страница или вариант не найдены")
    active = next((item for item in variants if item.is_active), current)
    return WebPageVariantsResponse(
        slug=slug,
        active_variant_key=active.variant_key,
        current_variant_key=current.variant_key,
        variants=[_variant_summary(item) for item in variants],
    )


@router.post("/api/web/pages/{slug}/variants", response_model=WebPageVariantsResponse)
async def create_web_page_variant(
    slug: str,
    body: WebPageVariantCreate,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_designer),
):
    source_variant, variants = await _resolve_variant(session, slug, body.from_variant_key)
    existing_keys = {variant.variant_key for variant in variants}
    variant_key = _generate_variant_key(existing_keys, body.key, body.name)
    if variant_key in existing_keys:
        raise HTTPException(400, "Вариант с таким ключом уже существует")

    variant_name = _normalize_variant_name(body.name, f"Вариант {len(variants) + 1}")
    new_variant = WebPageVariant(
        page_slug=slug,
        variant_key=variant_key,
        name=variant_name,
        is_active=False,
        theme_tokens=dict(source_variant.theme_tokens or {}),
    )
    session.add(new_variant)
    await session.flush()

    source_blocks = await _get_variant_blocks(session, source_variant.id)
    for block in source_blocks:
        session.add(
            WebPageVariantBlock(
                variant_id=new_variant.id,
                order=block.order,
                type=block.type,
                data=block.data,
            )
        )
    await session.flush()
    await bump_site_revision(session)
    await _audit_web_admin(
        session, identity, "variant.create", entity_type="page", entity_id=slug, metadata={"variant": variant_key}
    )

    refreshed = await _list_variants(session, slug)
    return WebPageVariantsResponse(
        slug=slug,
        active_variant_key=next((item.variant_key for item in refreshed if item.is_active), DEFAULT_VARIANT_KEY),
        current_variant_key=new_variant.variant_key,
        variants=[_variant_summary(item) for item in refreshed],
    )


@router.patch("/api/web/pages/{slug}/variants/{variant_key}", response_model=WebPageVariantsResponse)
async def update_web_page_variant(
    slug: str,
    variant_key: str,
    body: WebPageVariantUpdate,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_designer),
):
    current, variants = await _resolve_variant(session, slug, variant_key)
    if body.name is not None:
        current.name = _normalize_variant_name(body.name, current.name or current.variant_key)
    if body.make_active is True:
        variants = await _set_active_variant(session, slug, current.variant_key)
        current = next((item for item in variants if item.variant_key == current.variant_key), current)
    else:
        await session.flush()
        variants = await _list_variants(session, slug)

    await bump_site_revision(session)
    await _audit_web_admin(
        session,
        identity,
        "variant.update",
        entity_type="page",
        entity_id=slug,
        metadata={"variant": current.variant_key, "make_active": body.make_active is True},
    )
    active = next((item for item in variants if item.is_active), current)
    return WebPageVariantsResponse(
        slug=slug,
        active_variant_key=active.variant_key,
        current_variant_key=current.variant_key,
        variants=[_variant_summary(item) for item in variants],
    )


@router.delete("/api/web/pages/{slug}/variants/{variant_key}", response_model=WebPageVariantsResponse)
async def delete_web_page_variant(
    slug: str,
    variant_key: str,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_designer),
):
    current, variants = await _resolve_variant(session, slug, variant_key)
    if len(variants) <= 1:
        raise HTTPException(400, "Нельзя удалить единственный вариант страницы")

    replacement = next((item for item in variants if item.variant_key != current.variant_key), None)
    await session.execute(delete(WebPageVariant).where(WebPageVariant.id == current.id))
    await session.flush()

    if current.is_active and replacement is not None:
        replacement_variants = await _set_active_variant(session, slug, replacement.variant_key)
    else:
        replacement_variants = await _list_variants(session, slug)

    await bump_site_revision(session)
    await _audit_web_admin(
        session,
        identity,
        "variant.delete",
        entity_type="page",
        entity_id=slug,
        metadata={"variant": current.variant_key},
    )
    current_variant_key = replacement.variant_key if replacement is not None else DEFAULT_VARIANT_KEY
    active_variant_key = next(
        (item.variant_key for item in replacement_variants if item.is_active),
        current_variant_key,
    )
    return WebPageVariantsResponse(
        slug=slug,
        active_variant_key=active_variant_key,
        current_variant_key=current_variant_key,
        variants=[_variant_summary(item) for item in replacement_variants],
    )


@router.post("/api/web/upload", response_model=WebUploadResponse)
async def upload_media(
    file: UploadFile = File(...),
    identity=Depends(verify_identity_designer),
):
    """Upload image or video for landing blocks and return same-origin URL."""
    if not file.filename or "." not in file.filename:
        raise HTTPException(400, "Файл должен иметь расширение")
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            400,
            f"Разрешены только: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )
    if file.content_type:
        allowed_types = EXTENSION_CONTENT_TYPES.get(ext)
        if allowed_types and file.content_type.lower() not in allowed_types:
            raise HTTPException(
                400,
                f"Тип файла ({file.content_type}) не соответствует расширению ({ext})",
            )
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    chunks: list[bytes] = []
    size = 0
    for chunk in file.file:
        size += len(chunk)
        if size > MAX_FILE_SIZE:
            raise HTTPException(400, f"Размер файла не более {MAX_FILE_SIZE // (1024 * 1024)} МБ")
        chunks.append(chunk)
    name = f"{uuid.uuid4().hex}{ext}"
    path = UPLOAD_DIR / name
    file_data = b"".join(chunks)
    if ext == ".svg":
        file_data = _sanitize_svg(file_data)
    elif ext in _IMAGE_RESIZE_EXTENSIONS:
        from core.executor import run_cpu

        file_data = await run_cpu(_optimize_image_bytes, file_data, ext)
    with open(path, "wb") as f:
        f.write(file_data)
    url = f"/api/web/uploads/{name}"
    logger.info(
        "[WebUpload] admin={} file={} -> {} ({} bytes)",
        identity.id,
        file.filename,
        name,
        len(file_data),
    )
    return WebUploadResponse(url=url)


# ── Custom Element Builds ──


class CustomElementBuildCreate(BaseModel):
    label: str = ""
    slug: str = ""
    runtime: str = "react-component"
    source_kind: str = "inline-code"
    source_value: str = ""
    export_name: str = "default"
    props_schema_text: str = ""
    sample_props_text: str = ""
    events_text: str = ""
    notes: str = ""


class CustomElementBuildUpdate(BaseModel):
    status: str | None = None
    summary: str | None = None
    next_steps: list[str] | None = None
    artifact: dict | None = None
    upload_meta: dict | None = None
    worker_id: str | None = None


def _build_to_dict(b: WebCustomElementBuild) -> dict:
    return {
        "id": b.id,
        "label": b.label,
        "slug": b.slug,
        "runtime": b.runtime,
        "sourceKind": b.source_kind,
        "sourceValue": b.source_value,
        "exportName": b.export_name,
        "propsSchemaText": b.props_schema_text,
        "samplePropsText": b.sample_props_text,
        "eventsText": b.events_text,
        "notes": b.notes,
        "status": b.status,
        "summary": b.summary,
        "nextSteps": b.next_steps or [],
        "artifact": b.artifact,
        "upload": b.upload_meta,
        "workerId": b.worker_id,
        "workerClaimedAt": b.worker_claimed_at.isoformat() if b.worker_claimed_at else None,
        "completedAt": b.completed_at.isoformat() if b.completed_at else None,
        "createdAt": b.created_at.isoformat() if b.created_at else None,
        "updatedAt": b.updated_at.isoformat() if b.updated_at else None,
    }


@router.get("/api/web/custom-element-builds")
@router.get("/custom-element-builds")
async def list_custom_element_builds(
    session: AsyncSession = Depends(get_session),
    _identity=Depends(verify_identity_designer),
):
    result = await session.execute(select(WebCustomElementBuild).order_by(WebCustomElementBuild.created_at.desc()))
    builds = result.scalars().all()
    return [_build_to_dict(b) for b in builds]


@router.post("/api/web/custom-element-builds")
@router.post("/custom-element-builds")
async def create_custom_element_build(
    body: CustomElementBuildCreate,
    session: AsyncSession = Depends(get_session),
    _identity=Depends(verify_identity_designer),
):
    build = WebCustomElementBuild(
        id=str(uuid.uuid4()),
        label=body.label,
        slug=body.slug,
        runtime=body.runtime,
        source_kind=body.source_kind,
        source_value=body.source_value,
        export_name=body.export_name,
        props_schema_text=body.props_schema_text,
        sample_props_text=body.sample_props_text,
        events_text=body.events_text,
        notes=body.notes,
        status="queued",
    )
    session.add(build)
    return _build_to_dict(build)


@router.get("/api/web/custom-element-builds/{build_id}")
@router.get("/custom-element-builds/{build_id}")
async def get_custom_element_build(
    build_id: str,
    session: AsyncSession = Depends(get_session),
    _identity=Depends(verify_identity_designer),
):
    build = await session.get(WebCustomElementBuild, build_id)
    if not build:
        raise HTTPException(404, "Build not found")
    return _build_to_dict(build)


@router.patch("/api/web/custom-element-builds/{build_id}")
@router.patch("/custom-element-builds/{build_id}")
async def update_custom_element_build(
    build_id: str,
    body: CustomElementBuildUpdate,
    session: AsyncSession = Depends(get_session),
    _identity=Depends(verify_identity_designer),
):
    build = await session.get(WebCustomElementBuild, build_id)
    if not build:
        raise HTTPException(404, "Build not found")
    if body.status is not None:
        build.status = body.status
    if body.summary is not None:
        build.summary = body.summary
    if body.next_steps is not None:
        build.next_steps = body.next_steps
    if body.artifact is not None:
        build.artifact = body.artifact
    if body.upload_meta is not None:
        build.upload_meta = body.upload_meta
    if body.worker_id is not None:
        build.worker_id = body.worker_id
    return _build_to_dict(build)


@router.delete("/api/web/custom-element-builds/{build_id}")
@router.delete("/custom-element-builds/{build_id}")
async def delete_custom_element_build(
    build_id: str,
    session: AsyncSession = Depends(get_session),
    _identity=Depends(verify_identity_designer),
):
    build = await session.get(WebCustomElementBuild, build_id)
    if not build:
        raise HTTPException(404, "Build not found")
    await session.delete(build)
    return {"ok": True}


# ── Flow Analytics ──


_SENSITIVE_KEY_RE = re.compile(
    r"(token|password|secret|api[_-]?key|authorization|cookie|session|auth|credential|bearer|pass|passwd|access[_-]?token|refresh[_-]?token|phone|email|hash|private|pin)",
    re.IGNORECASE,
)
_REDACTED = "[redacted]"
_MAX_REDACT_DEPTH = 6


def _redact_sensitive(value, depth: int = 0):
    if depth >= _MAX_REDACT_DEPTH:
        return _REDACTED
    if isinstance(value, dict):
        return {
            k: (_REDACTED if isinstance(k, str) and _SENSITIVE_KEY_RE.search(k) else _redact_sensitive(v, depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive(v, depth + 1) for v in value[:100]]
    if isinstance(value, str) and len(value) > 2000:
        return value[:2000] + "…"
    return value


class FlowEventBatch(BaseModel):
    events: list[dict]


@router.post("/api/web/analytics/flow-events")
@router.post("/analytics/flow-events")
async def ingest_flow_events(
    body: FlowEventBatch,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    try:
        from api.v2.routes.auth._common import _client_ip
        from api.v2.routes.auth._fallback_limiter import check_and_increment
        from core.redis_cache import cache_incr_checked

        ip = _client_ip(request) or "unknown"
        count, redis_ok = await cache_incr_checked(f"analytics_rate:{ip}", 60)
        if not redis_ok:
            count = check_and_increment(f"analytics_rate:{ip}", 60, 60)
        if count > 60:
            raise HTTPException(status_code=429, detail="Too many events")
    except HTTPException:
        raise
    except Exception:
        pass
    server_identity = await _identity_from_cookie(session, request)
    if server_identity is not None and getattr(server_identity, "is_admin", False):
        return {"ingested": 0}
    server_authenticated = server_identity is not None
    valid_flows = await _known_flow_ids(session)
    created = 0
    for raw in body.events[:100]:
        flow_id = str(raw.get("flowId", ""))[:64]
        node_id = str(raw.get("nodeId", ""))[:64]
        event_type = str(raw.get("eventType", ""))[:32]
        if not flow_id or not node_id or not event_type:
            continue
        if valid_flows and flow_id not in valid_flows:
            continue
        metadata = raw.get("collectedDataSnapshot")
        if isinstance(metadata, dict):
            metadata = _redact_sensitive(metadata)
        else:
            metadata = None
        ev = WebFlowEvent(
            id=str(uuid.uuid4()),
            flow_id=flow_id,
            node_id=node_id,
            node_type=str(raw.get("nodeType", ""))[:32],
            event_type=event_type,
            ab_variant=(str(raw.get("abVariant"))[:16] if raw.get("abVariant") else None),
            device=(str(raw.get("device"))[:16] if raw.get("device") else None),
            locale=(str(raw.get("locale"))[:8] if raw.get("locale") else None),
            authenticated=server_authenticated,
            event_metadata=metadata,
        )
        session.add(ev)
        created += 1
    return {"ingested": created}


@router.get("/api/web/analytics/flow-funnel/{flow_id}")
@router.get("/analytics/flow-funnel/{flow_id}")
async def get_flow_funnel(
    flow_id: str,
    days: int = Query(default=30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
    _identity=Depends(verify_identity_designer),
):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        await session.execute(
            select(
                WebFlowEvent.node_id,
                WebFlowEvent.node_type,
                WebFlowEvent.event_type,
                func.count().label("cnt"),
            )
            .where(WebFlowEvent.flow_id == flow_id)
            .where(WebFlowEvent.created_at >= since)
            .group_by(WebFlowEvent.node_id, WebFlowEvent.node_type, WebFlowEvent.event_type)
        )
    ).all()

    nodes: dict[str, dict] = {}
    for node_id, node_type, event_type, cnt in rows:
        if node_id not in nodes:
            nodes[node_id] = {"nodeId": node_id, "nodeType": node_type, "entered": 0, "exited": 0, "completed": 0}
        if event_type == "flow_step_entered":
            nodes[node_id]["entered"] = cnt
        elif event_type == "flow_step_exited":
            nodes[node_id]["exited"] = cnt
        elif event_type == "flow_completed":
            nodes[node_id]["completed"] = cnt

    flow = await session.get(WebFlow, flow_id)
    if flow and flow.nodes:
        node_order = {n["id"]: i for i, n in enumerate(flow.nodes) if isinstance(n, dict)}
    else:
        node_order = {}

    funnel = sorted(nodes.values(), key=lambda n: node_order.get(n["nodeId"], 999))

    for i, node in enumerate(funnel):
        prev_entered = funnel[i - 1]["entered"] if i > 0 else node["entered"]
        node["dropOff"] = round((1 - node["entered"] / prev_entered) * 100, 1) if prev_entered > 0 else 0

    return {"flowId": flow_id, "days": days, "funnel": funnel}


async def _trackable_page_slugs(session: AsyncSession) -> set[str]:
    rows = await session.execute(
        select(WebPageVariant.page_slug)
        .join(WebPageVariantBlock, WebPageVariantBlock.variant_id == WebPageVariant.id)
        .distinct()
    )
    return {slug for (slug,) in rows.all()} | set(KNOWN_PAGE_SLUGS)


async def _known_flow_ids(session: AsyncSession) -> set[str]:
    rows = await session.execute(select(WebFlow.id))
    return {fid for (fid,) in rows.all()}


class PageViewBatch(BaseModel):
    views: list[dict]


@router.post("/api/web/analytics/page-views")
@router.post("/analytics/page-views")
async def ingest_page_views(
    body: PageViewBatch,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    try:
        from api.v2.routes.auth._common import _client_ip
        from api.v2.routes.auth._fallback_limiter import check_and_increment
        from core.redis_cache import cache_incr_checked

        ip = _client_ip(request) or "unknown"
        count, redis_ok = await cache_incr_checked(f"analytics_pv_rate:{ip}", 60)
        if not redis_ok:
            count = check_and_increment(f"analytics_pv_rate:{ip}", 60, 120)
        if count > 120:
            raise HTTPException(status_code=429, detail="Too many events")
    except HTTPException:
        raise
    except Exception:
        pass
    server_identity = await _identity_from_cookie(session, request)
    if server_identity is not None and getattr(server_identity, "is_admin", False):
        return {"ingested": 0}
    server_authenticated = server_identity is not None
    valid_slugs = await _trackable_page_slugs(session)
    created = 0
    for raw in body.views[:50]:
        visitor_id = str(raw.get("visitorId", ""))[:36].strip()
        page_slug = str(raw.get("pageSlug", ""))[:64].strip()
        if not visitor_id or not page_slug or page_slug not in valid_slugs:
            continue
        pv = WebPageView(
            id=str(uuid.uuid4()),
            visitor_id=visitor_id,
            page_slug=page_slug,
            referrer=(str(raw.get("referrer"))[:255] if raw.get("referrer") else None),
            utm_source=(str(raw.get("utmSource"))[:64] if raw.get("utmSource") else None),
            utm_medium=(str(raw.get("utmMedium"))[:64] if raw.get("utmMedium") else None),
            utm_campaign=(str(raw.get("utmCampaign"))[:64] if raw.get("utmCampaign") else None),
            device=(str(raw.get("device"))[:16] if raw.get("device") else None),
            locale=(str(raw.get("locale"))[:8] if raw.get("locale") else None),
            authenticated=server_authenticated,
            source=((lambda s: s if s in ("webapp", "pwa") else "web")(str(raw.get("source") or "").strip().lower())),
            ab_variant=(str(raw.get("abVariant"))[:16] if raw.get("abVariant") else None),
        )
        session.add(pv)
        created += 1
    return {"ingested": created}


@router.delete("/api/web/analytics/page-views")
@router.delete("/analytics/page-views")
async def reset_analytics_page_views(
    session: AsyncSession = Depends(get_session),
    _identity=Depends(verify_identity_designer),
):
    """Очищает накопленные просмотры страниц (тестовые/девелоперские данные).

    Удаляет только web_page_views — реальные регистрации/платежи не трогаются.
    """
    result = await session.execute(delete(WebPageView))
    await _audit_web_admin(
        session,
        _identity,
        "analytics.reset",
        entity_type="analytics",
        entity_id="page_views",
        metadata={"deleted": int(result.rowcount or 0)},
    )
    return {"deleted": int(result.rowcount or 0)}


@router.get("/api/web/analytics/overview")
@router.get("/analytics/overview")
async def get_analytics_overview(
    days: int = Query(default=30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
    _identity=Depends(verify_identity_designer),
):
    from database.models import (
        CouponUsage,
        GiftUsage,
        Identity,
        Key,
        Payment,
        Referral,
        Tariff,
        TrackingSource,
        User,
    )

    since = datetime.now(timezone.utc) - timedelta(days=days)
    since_naive = since.replace(tzinfo=None)

    # Внутренние «платежи» (бонусы/ручная выдача) — не реальный доход, исключаем из выручки.
    internal_systems = ("referral", "cashback", "coupon", "admin")
    real_income = Payment.payment_system.notin_(internal_systems)

    day_col = func.date_trunc("day", WebPageView.created_at).label("day")
    daily_rows = (
        await session.execute(
            select(
                day_col,
                func.count().label("views"),
                func.count(func.distinct(WebPageView.visitor_id)).label("visitors"),
            )
            .where(WebPageView.created_at >= since)
            .group_by(day_col)
            .order_by(day_col)
        )
    ).all()

    totals_row = (
        await session.execute(
            select(
                func.count().label("views"),
                func.count(func.distinct(WebPageView.visitor_id)).label("visitors"),
            ).where(WebPageView.created_at >= since)
        )
    ).first()

    source_rows = (
        await session.execute(
            select(
                WebPageView.source,
                func.count().label("views"),
                func.count(func.distinct(WebPageView.visitor_id)).label("visitors"),
            )
            .where(WebPageView.created_at >= since)
            .group_by(WebPageView.source)
        )
    ).all()
    src_split = {
        "webapp": {"views": 0, "visitors": 0},
        "pwa": {"views": 0, "visitors": 0},
        "web": {"views": 0, "visitors": 0},
    }
    for s_row in source_rows:
        key = s_row.source if s_row.source in ("webapp", "pwa") else "web"
        src_split[key]["views"] += int(s_row.views or 0)
        src_split[key]["visitors"] += int(s_row.visitors or 0)

    daily_src_rows = (
        await session.execute(
            select(
                day_col,
                WebPageView.source,
                func.count().label("views"),
                func.count(func.distinct(WebPageView.visitor_id)).label("visitors"),
            )
            .where(WebPageView.created_at >= since)
            .group_by(day_col, WebPageView.source)
            .order_by(day_col)
        )
    ).all()
    daily_web: dict[str, dict[str, int]] = {}
    daily_webapp: dict[str, dict[str, int]] = {}
    daily_pwa: dict[str, dict[str, int]] = {}
    for d_row in daily_src_rows:
        d_key = d_row.day.strftime("%Y-%m-%d")
        bucket = daily_webapp if (d_row.source == "webapp") else (daily_pwa if (d_row.source == "pwa") else daily_web)
        cur = bucket.setdefault(d_key, {"views": 0, "visitors": 0})
        cur["views"] += int(d_row.views or 0)
        cur["visitors"] += int(d_row.visitors or 0)

    top_pages = (
        await session.execute(
            select(
                WebPageView.page_slug,
                func.count().label("views"),
                func.count(func.distinct(WebPageView.visitor_id)).label("visitors"),
            )
            .where(WebPageView.created_at >= since)
            .group_by(WebPageView.page_slug)
            .order_by(func.count().desc())
            .limit(10)
        )
    ).all()

    referrers = (
        await session.execute(
            select(
                WebPageView.referrer,
                func.count(func.distinct(WebPageView.visitor_id)).label("visitors"),
            )
            .where(WebPageView.created_at >= since)
            .where(WebPageView.referrer.isnot(None))
            .group_by(WebPageView.referrer)
            .order_by(func.count(func.distinct(WebPageView.visitor_id)).desc())
            .limit(10)
        )
    ).all()

    devices = (
        await session.execute(
            select(
                WebPageView.device,
                func.count(func.distinct(WebPageView.visitor_id)).label("visitors"),
            )
            .where(WebPageView.created_at >= since)
            .group_by(WebPageView.device)
            .order_by(func.count(func.distinct(WebPageView.visitor_id)).desc())
        )
    ).all()

    ab_rows = (
        await session.execute(
            select(
                WebPageView.ab_variant,
                func.count().label("views"),
                func.count(func.distinct(WebPageView.visitor_id)).label("visitors"),
            )
            .where(WebPageView.created_at >= since)
            .where(WebPageView.ab_variant.isnot(None))
            .group_by(WebPageView.ab_variant)
            .order_by(WebPageView.ab_variant)
        )
    ).all()
    ab_checkout = dict(
        (
            await session.execute(
                select(WebPageView.ab_variant, func.count(func.distinct(WebPageView.visitor_id)))
                .where(WebPageView.created_at >= since)
                .where(WebPageView.page_slug == "checkout")
                .where(WebPageView.ab_variant.isnot(None))
                .group_by(WebPageView.ab_variant)
            )
        ).all()
    )

    checkout_visitors = (
        await session.scalar(
            select(func.count(func.distinct(WebPageView.visitor_id)))
            .where(WebPageView.created_at >= since)
            .where(WebPageView.page_slug == "checkout")
        )
    ) or 0

    login_visitors = (
        await session.scalar(
            select(func.count(func.distinct(WebPageView.visitor_id)))
            .where(WebPageView.created_at >= since)
            .where(WebPageView.page_slug == "login")
        )
    ) or 0

    errors_unresolved = (
        await session.scalar(select(func.count()).select_from(WebErrorReport).where(WebErrorReport.resolved.is_(False)))
    ) or 0
    errors_new = (
        await session.scalar(
            select(func.count()).select_from(WebErrorReport).where(WebErrorReport.first_seen_at >= since)
        )
    ) or 0
    top_errors_rows = (
        await session.execute(
            select(
                WebErrorReport.error_name,
                WebErrorReport.error_message,
                WebErrorReport.count,
                WebErrorReport.last_seen_at,
            )
            .where(WebErrorReport.resolved.is_(False))
            .order_by(WebErrorReport.count.desc())
            .limit(5)
        )
    ).all()

    registrations = (
        await session.scalar(select(func.count()).select_from(Identity).where(Identity.created_at >= since_naive))
    ) or 0
    registrations_tg = (
        await session.scalar(
            select(func.count())
            .select_from(Identity)
            .where(Identity.created_at >= since_naive, Identity.tg_id.isnot(None))
        )
    ) or 0
    registrations_web = int(registrations) - int(registrations_tg)

    web_payment_marker = Payment.metadata_["payment_flow"].astext.isnot(None)
    payments_row = (
        await session.execute(
            select(
                func.count().label("cnt"),
                func.count(func.distinct(Payment.user_id)).label("payers"),
                func.coalesce(func.sum(Payment.amount), 0).label("revenue"),
            )
            .where(Payment.created_at >= since_naive)
            .where(Payment.status == "success")
            .where(real_income)
            .where(web_payment_marker)
        )
    ).first()

    all_payments_row = (
        await session.execute(
            select(
                func.count().label("cnt"),
                func.coalesce(func.sum(Payment.amount), 0).label("revenue"),
            )
            .where(Payment.created_at >= since_naive)
            .where(Payment.status == "success")
            .where(real_income)
        )
    ).first()

    active_keys = (
        await session.scalar(
            select(func.count())
            .select_from(Key)
            .where(Key.expiry_time > int(datetime.now(timezone.utc).timestamp() * 1000))
        )
    ) or 0

    reg_day_col = func.date_trunc("day", Identity.created_at).label("day")
    daily_reg_rows = (
        await session.execute(
            select(reg_day_col, func.count().label("cnt"))
            .where(Identity.created_at >= since_naive)
            .group_by(reg_day_col)
            .order_by(reg_day_col)
        )
    ).all()

    pay_day_col = func.date_trunc("day", Payment.created_at).label("day")
    daily_pay_rows = (
        await session.execute(
            select(
                pay_day_col,
                func.count().label("cnt"),
                func.coalesce(func.sum(Payment.amount), 0).label("revenue"),
                func.count().filter(web_payment_marker).label("site_cnt"),
                func.coalesce(func.sum(Payment.amount).filter(web_payment_marker), 0).label("site_revenue"),
            )
            .where(Payment.created_at >= since_naive)
            .where(Payment.status == "success")
            .where(real_income)
            .group_by(pay_day_col)
            .order_by(pay_day_col)
        )
    ).all()

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    int(since.timestamp() * 1000)

    bot_users_total = (
        await session.scalar(select(func.count()).select_from(User).where(User.created_at >= since_naive))
    ) or 0
    bot_user_day = func.date_trunc("day", User.created_at).label("day")
    daily_bot_rows = (
        await session.execute(
            select(bot_user_day, func.count().label("cnt"))
            .where(User.created_at >= since_naive)
            .group_by(bot_user_day)
            .order_by(bot_user_day)
        )
    ).all()

    src_name_map = dict((await session.execute(select(TrackingSource.code, TrackingSource.name))).all())
    bot_source_rows = (
        await session.execute(
            select(User.source_code, func.count().label("cnt"))
            .where(User.created_at >= since_naive)
            .group_by(User.source_code)
            .order_by(func.count().desc())
            .limit(8)
        )
    ).all()

    method_col = func.lower(func.coalesce(Payment.payment_system, "unknown")).label("method")
    method_rows = (
        await session.execute(
            select(
                method_col,
                func.count().label("cnt"),
                func.coalesce(func.sum(Payment.amount), 0).label("rev"),
            )
            .where(Payment.created_at >= since_naive)
            .where(Payment.status == "success")
            .where(real_income)
            .group_by(method_col)
            .order_by(func.count().desc())
        )
    ).all()

    all_payers = (
        await session.scalar(
            select(func.count(func.distinct(Payment.user_id)))
            .where(Payment.created_at >= since_naive)
            .where(Payment.status == "success")
            .where(real_income)
        )
    ) or 0
    first_pay_sq = (
        select(Payment.user_id, func.min(Payment.created_at).label("first"))
        .where(Payment.status == "success")
        .where(real_income)
        .group_by(Payment.user_id)
    ).subquery()
    new_buyers = (
        await session.scalar(select(func.count()).select_from(first_pay_sq).where(first_pay_sq.c.first >= since_naive))
    ) or 0
    total_revenue = float(all_payments_row.revenue or 0) if all_payments_row else 0.0

    coupons_used = (
        await session.scalar(select(func.count()).select_from(CouponUsage).where(CouponUsage.used_at >= since_naive))
    ) or 0
    gifts_used = (
        await session.scalar(select(func.count()).select_from(GiftUsage).where(GiftUsage.used_at >= since_naive))
    ) or 0
    referrals_cnt = (
        await session.scalar(
            select(func.count())
            .select_from(Referral)
            .join(User, Referral.referred_user_id == User.id)
            .where(User.created_at >= since_naive)
        )
    ) or 0

    from database.subscription_events import get_retention_metrics, get_subscription_dynamics

    sub_dynamics = await get_subscription_dynamics(session, days)
    retention = await get_retention_metrics(session, days)

    expiring_soon = (
        await session.scalar(
            select(func.count())
            .select_from(Key)
            .where(Key.expiry_time > now_ms)
            .where(Key.expiry_time <= now_ms + 7 * 86400 * 1000)
        )
    ) or 0

    tariff_rows = (
        await session.execute(
            select(Tariff.name, func.count().label("cnt"))
            .select_from(Key)
            .join(Tariff, Key.tariff_id == Tariff.id, isouter=True)
            .where(Key.expiry_time > now_ms)
            .group_by(Tariff.name)
            .order_by(func.count().desc())
            .limit(8)
        )
    ).all()
    server_rows = (
        await session.execute(
            select(Key.server_id, func.count().label("cnt"))
            .where(Key.expiry_time > now_ms)
            .group_by(Key.server_id)
            .order_by(func.count().desc())
            .limit(8)
        )
    ).all()

    return {
        "days": days,
        "totals": {
            "views": int(totals_row.views or 0) if totals_row else 0,
            "visitors": int(totals_row.visitors or 0) if totals_row else 0,
            "viewsWeb": src_split["web"]["views"],
            "viewsWebapp": src_split["webapp"]["views"],
            "viewsPwa": src_split["pwa"]["views"],
            "visitorsWeb": src_split["web"]["visitors"],
            "visitorsWebapp": src_split["webapp"]["visitors"],
            "visitorsPwa": src_split["pwa"]["visitors"],
            "registrations": int(registrations),
            "registrationsTg": int(registrations_tg),
            "registrationsWeb": int(registrations_web),
            "checkoutVisitors": int(checkout_visitors),
            "payments": int(payments_row.cnt or 0) if payments_row else 0,
            "payers": int(payments_row.payers or 0) if payments_row else 0,
            "revenueRub": float(payments_row.revenue or 0) if payments_row else 0.0,
            "totalPayments": int(all_payments_row.cnt or 0) if all_payments_row else 0,
            "totalRevenueRub": float(all_payments_row.revenue or 0) if all_payments_row else 0.0,
            "activeKeys": int(active_keys),
            "botUsers": int(bot_users_total),
            "allPayers": int(all_payers),
            "newBuyers": int(new_buyers),
            "arpuRub": (total_revenue / all_payers) if all_payers else 0.0,
            "couponsUsed": int(coupons_used),
            "giftsActivated": int(gifts_used),
            "referrals": int(referrals_cnt),
            "expiringSoon": int(expiring_soon),
        },
        "daily": [
            {
                "date": row.day.strftime("%Y-%m-%d"),
                "views": int(row.views),
                "visitors": int(row.visitors),
            }
            for row in daily_rows
        ],
        "dailyWeb": [{"date": d, "views": v["views"], "visitors": v["visitors"]} for d, v in sorted(daily_web.items())],
        "dailyWebapp": [
            {"date": d, "views": v["views"], "visitors": v["visitors"]} for d, v in sorted(daily_webapp.items())
        ],
        "dailyPwa": [{"date": d, "views": v["views"], "visitors": v["visitors"]} for d, v in sorted(daily_pwa.items())],
        "dailyRegistrations": [{"date": row.day.strftime("%Y-%m-%d"), "count": int(row.cnt)} for row in daily_reg_rows],
        "dailyPayments": [
            {
                "date": row.day.strftime("%Y-%m-%d"),
                "payments": int(row.cnt),
                "revenueRub": float(row.revenue or 0),
                "sitePayments": int(row.site_cnt or 0),
                "siteRevenueRub": float(row.site_revenue or 0),
            }
            for row in daily_pay_rows
        ],
        "topPages": [
            {"slug": row.page_slug, "views": int(row.views), "visitors": int(row.visitors)} for row in top_pages
        ],
        "referrers": [{"source": row.referrer, "visitors": int(row.visitors)} for row in referrers],
        "devices": [{"device": row.device or "unknown", "visitors": int(row.visitors)} for row in devices],
        "abVariants": [
            {
                "variant": row.ab_variant,
                "views": int(row.views),
                "visitors": int(row.visitors),
                "checkoutVisitors": int(ab_checkout.get(row.ab_variant, 0) or 0),
            }
            for row in ab_rows
        ],
        "dailyBotUsers": [{"date": r.day.strftime("%Y-%m-%d"), "count": int(r.cnt)} for r in daily_bot_rows],
        "botSources": [
            {"source": (src_name_map.get(r.source_code) or r.source_code or "Прямой"), "users": int(r.cnt)}
            for r in bot_source_rows
        ],
        "paymentMethods": [
            {"method": r.method or "unknown", "payments": int(r.cnt), "revenueRub": float(r.rev or 0)}
            for r in method_rows
        ],
        "dailySubs": [
            {"date": e["date"], "created": e["created"], "expired": e["expired"]} for e in sub_dynamics["dailyEvents"]
        ],
        "activeTrend": sub_dynamics["activeTrend"],
        "retention": retention,
        "funnel": [
            {"key": "visitors", "value": int(totals_row.visitors or 0) if totals_row else 0},
            {"key": "login", "value": int(login_visitors)},
            {"key": "checkout", "value": int(checkout_visitors)},
            {"key": "paid", "value": int(payments_row.payers or 0) if payments_row else 0},
        ],
        "errors": {
            "unresolved": int(errors_unresolved),
            "newInPeriod": int(errors_new),
            "top": [
                {
                    "name": r[0] or "Error",
                    "message": (r[1] or "")[:120],
                    "count": int(r[2] or 0),
                    "lastSeen": r[3].isoformat() if r[3] else "",
                }
                for r in top_errors_rows
            ],
        },
        "tariffs": [{"tariff": r.name or "Без тарифа", "count": int(r.cnt)} for r in tariff_rows],
        "servers": [{"server": r.server_id or "—", "count": int(r.cnt)} for r in server_rows],
    }


# ── Error aggregation (in-house Sentry) ──


def _error_signature(name: str, message: str, stack: str | None, url: str | None) -> str:
    """Группировочная подпись: name + первая stack-frame + pathname."""
    first_frame = ""
    if stack:
        for line in stack.split("\n"):
            s = line.strip()
            if s.startswith("at ") or "webpack-internal" in s or ".tsx:" in s or ".ts:" in s or ".js:" in s:
                first_frame = s[:200]
                break
    pathname = ""
    if url:
        try:
            from urllib.parse import urlparse

            pathname = urlparse(url).path[:100]
        except Exception:
            pass
    key = f"{name}|{first_frame}|{pathname}|{message[:120]}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def _sanitize_http_url(value: str | None) -> str | None:
    if not value:
        return None
    trimmed = str(value).strip()
    if not trimmed:
        return None
    lowered = trimmed.lower()
    if not (lowered.startswith(("http://", "https://", "/"))):
        return None
    return trimmed[:500]


class ErrorReportIngest(BaseModel):
    name: str = ""
    message: str
    stack: str | None = None
    url: str | None = None
    userAgent: str | None = None
    tag: str | None = None
    context: dict | None = None


_ERROR_ALERT_THRESHOLDS = frozenset({10, 50, 200, 1000})
_NEW_ERROR_ALERTS_PER_HOUR = 6


async def _alert_web_error(name: str, message: str, url: str | None, count: int, is_new: bool) -> None:
    """Шлёт админам уведомление о новой ошибке сайта или о всплеске по счётчику.
    Новые ошибки троттлятся глобально, чтобы не заспамить при запуске."""
    try:
        if is_new:
            try:
                from api.v2.routes.auth._fallback_limiter import check_and_increment
                from core.redis_cache import cache_incr_checked

                fired, redis_ok = await cache_incr_checked("web_err_new_alert:hour", 3600)
                if not redis_ok:
                    fired = check_and_increment("web_err_new_alert:hour", _NEW_ERROR_ALERTS_PER_HOUR, 3600)
                if fired > _NEW_ERROR_ALERTS_PER_HOUR:
                    return
            except Exception:
                pass

        head = "🆕 Новая ошибка сайта" if is_new else f"📈 Всплеск ошибки сайта (×{count})"
        parts = [head, f"{(name or 'Error')[:120]}: {(message or '')[:300]}"]
        if url:
            parts.append(f"URL: {url[:200]}")
        if not is_new:
            parts.append(f"Всего повторов: {count}")
        parts.append("Подробнее — в админ-панели → Логи и здоровье → ошибки.")
        from services.admin_alert import send_admin_alert

        await send_admin_alert("\n".join(parts))
    except Exception as exc:
        logger.warning("[WebErrorAlert] не удалось отправить алерт: {}", exc)


@router.post("/api/web/error-reports")
@router.post("/error-reports")
async def ingest_error_report(
    body: ErrorReportIngest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    try:
        from api.v2.routes.auth._common import _client_ip
        from api.v2.routes.auth._fallback_limiter import check_and_increment
        from core.redis_cache import cache_incr_checked

        ip = _client_ip(request) or "unknown"
        count, redis_ok = await cache_incr_checked(f"error_report_rate:{ip}", 60)
        if not redis_ok:
            count = check_and_increment(f"error_report_rate:{ip}", 30, 60)
        if count > 30:
            raise HTTPException(status_code=429, detail="Too many error reports")
    except HTTPException:
        raise
    except Exception:
        pass

    server_identity = await _identity_from_cookie(session, request)
    server_identity_id = getattr(server_identity, "id", None) if server_identity else None

    safe_context = None
    if isinstance(body.context, dict):
        safe_context = _redact_sensitive(body.context)

    safe_url = _sanitize_http_url(body.url)

    signature = _error_signature(body.name, body.message, body.stack, safe_url)

    existing = (
        await session.execute(select(WebErrorReport).where(WebErrorReport.signature == signature))
    ).scalar_one_or_none()

    if existing:
        existing.count += 1
        existing.last_seen_at = datetime.now(timezone.utc)
        existing.resolved = False
        if safe_context is not None:
            existing.last_context = safe_context
        if server_identity_id:
            existing.last_identity_id = server_identity_id[:36]
        if existing.count in _ERROR_ALERT_THRESHOLDS:
            await _alert_web_error(
                existing.error_name, existing.error_message, existing.url, existing.count, is_new=False
            )
        return {"ok": True, "id": existing.id, "count": existing.count, "deduplicated": True}

    try:
        from api.v2.routes.auth._common import _client_ip
        from api.v2.routes.auth._fallback_limiter import check_and_increment as _sig_check
        from core.redis_cache import cache_incr_checked as _sig_cache

        ip = _client_ip(request) or "unknown"
        unique_key = f"error_sig_unique:{ip}"
        count_uniq, redis_ok = await _sig_cache(unique_key, 3600)
        if not redis_ok:
            count_uniq = _sig_check(unique_key, 20, 3600)
        if count_uniq > 20:
            raise HTTPException(status_code=429, detail="Too many distinct errors")
    except HTTPException:
        raise
    except Exception:
        pass

    report = WebErrorReport(
        id=str(uuid.uuid4()),
        signature=signature,
        error_name=body.name[:255] if body.name else "",
        error_message=body.message[:4000] if body.message else "",
        stack=body.stack[:16000] if body.stack else None,
        url=safe_url,
        user_agent=body.userAgent[:500] if body.userAgent else None,
        tag=body.tag[:64] if body.tag else None,
        last_identity_id=server_identity_id[:36] if server_identity_id else None,
        last_context=safe_context,
        count=1,
        resolved=False,
    )
    session.add(report)
    await _alert_web_error(report.error_name, report.error_message, report.url, 1, is_new=True)
    return {"ok": True, "id": report.id, "count": 1, "deduplicated": False}


@router.get("/api/web/error-reports")
@router.get("/error-reports")
async def list_error_reports(
    session: AsyncSession = Depends(get_session),
    _identity=Depends(verify_identity_designer),
    resolved: bool | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    q = select(WebErrorReport).order_by(WebErrorReport.last_seen_at.desc())
    if resolved is not None:
        q = q.where(WebErrorReport.resolved == resolved)
    q = q.offset(offset).limit(limit)
    rows = (await session.execute(q)).scalars().all()
    return [
        {
            "id": r.id,
            "signature": r.signature,
            "errorName": r.error_name,
            "errorMessage": r.error_message,
            "stack": r.stack,
            "url": r.url,
            "userAgent": r.user_agent,
            "tag": r.tag,
            "lastIdentityId": r.last_identity_id,
            "lastContext": r.last_context,
            "count": r.count,
            "resolved": r.resolved,
            "firstSeenAt": r.first_seen_at.isoformat() if r.first_seen_at else None,
            "lastSeenAt": r.last_seen_at.isoformat() if r.last_seen_at else None,
        }
        for r in rows
    ]


class ErrorReportPatch(BaseModel):
    resolved: bool | None = None


@router.patch("/api/web/error-reports/{report_id}")
@router.patch("/error-reports/{report_id}")
async def update_error_report(
    report_id: str,
    body: ErrorReportPatch,
    session: AsyncSession = Depends(get_session),
    _identity=Depends(verify_identity_designer),
):
    report = await session.get(WebErrorReport, report_id)
    if not report:
        raise HTTPException(404, "Not found")
    if body.resolved is not None:
        report.resolved = body.resolved
    return {"ok": True, "resolved": report.resolved}


@router.delete("/api/web/error-reports/{report_id}")
@router.delete("/error-reports/{report_id}")
async def delete_error_report(
    report_id: str,
    session: AsyncSession = Depends(get_session),
    _identity=Depends(verify_identity_designer),
):
    report = await session.get(WebErrorReport, report_id)
    if not report:
        raise HTTPException(404, "Not found")
    await session.delete(report)
    return {"ok": True}


@router.post("/api/web/error-reports/resolve-all")
@router.post("/error-reports/resolve-all")
async def resolve_all_error_reports(
    session: AsyncSession = Depends(get_session),
    _identity=Depends(verify_identity_designer),
):
    from sqlalchemy import update as sa_update

    res = await session.execute(
        sa_update(WebErrorReport).where(WebErrorReport.resolved.is_(False)).values(resolved=True)
    )
    return {"ok": True, "updated": int(res.rowcount or 0)}


@router.delete("/api/web/error-reports")
@router.delete("/error-reports")
async def delete_error_reports_bulk(
    resolved: bool | None = Query(None),
    session: AsyncSession = Depends(get_session),
    _identity=Depends(verify_identity_designer),
):
    from sqlalchemy import delete as sa_delete

    stmt = sa_delete(WebErrorReport)
    if resolved is not None:
        stmt = stmt.where(WebErrorReport.resolved.is_(resolved))
    res = await session.execute(stmt)
    return {"ok": True, "deleted": int(res.rowcount or 0)}


@router.post("/api/web/install-default-design")
async def install_default_design(
    session: AsyncSession = Depends(get_session),
    _identity=Depends(verify_identity_designer),
):
    from database.web_default_seed import seed_default_site

    try:
        seeded = await seed_default_site(session, force=True)
        await session.flush()
    except Exception as e:
        logger.exception("[install_default_design] seed_default_site упал: %s", e)
        raise HTTPException(status_code=500, detail=f"install_default_design: {type(e).__name__}: {e}")
    await bump_site_revision(session)
    await _audit_web_admin(session, _identity, "design.install_default", entity_type="site", entity_id="default")
    return {"ok": True, "seeded": seeded}


_PACK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


_BUILTIN_PACK_IDS = {"core", "cyber-mono", "capybara", "default"}


@router.get("/api/web/packs")
async def list_packs(
    session: AsyncSession = Depends(get_session),
    _identity=Depends(verify_identity_designer),
):
    """Список своих (не встроенных) сохранённых наборов + статус сохранения встроенных дизайн-паков."""
    from database.web_default_seed import has_builtin_pack_file, list_custom_pack_designs, load_pack_design

    custom = await list_custom_pack_designs(session, _BUILTIN_PACK_IDS)
    builtin_saved: dict[str, bool] = {}
    for pid in ("cyber-mono", "capybara"):
        builtin_saved[pid] = bool(await load_pack_design(session, pid)) or has_builtin_pack_file(pid)
    return {"custom": custom, "builtinSaved": builtin_saved}


@router.get("/api/web/packs/{pack}/design")
async def get_pack_design_status(
    pack: str,
    session: AsyncSession = Depends(get_session),
    _identity=Depends(verify_identity_designer),
):
    """Сохранён ли дизайн набора (для состояния кнопки «Установить»)."""
    if not _PACK_ID_RE.match(pack):
        raise HTTPException(status_code=400, detail="Некорректный id набора")
    if pack == "default":
        return {"pack": pack, "saved": True, "builtin": True}
    from database.web_default_seed import load_pack_design

    site = await load_pack_design(session, pack)
    return {"pack": pack, "saved": bool(site), "builtin": pack in _BUILTIN_PACK_IDS}


@router.post("/api/web/packs")
async def create_custom_pack(
    request: Request,
    session: AsyncSession = Depends(get_session),
    _identity=Depends(verify_identity_designer),
):
    """Создаёт новый свой набор из текущего сайта (захват) с заданным именем."""
    from uuid import uuid4

    from database.web_default_seed import capture_and_store_pack_design

    try:
        body = await request.json()
    except Exception:
        body = {}
    name = str((body or {}).get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Укажите название набора")
    description = str((body or {}).get("description") or "").strip()
    pack_id = "c" + uuid4().hex[:20]
    try:
        await capture_and_store_pack_design(session, pack_id, name=name, description=description)
        await session.flush()
    except Exception as e:
        logger.exception("[create_custom_pack] упал: %s", e)
        raise HTTPException(status_code=500, detail=f"create_custom_pack: {type(e).__name__}: {e}")
    await _audit_web_admin(session, _identity, "design.create_pack", entity_type="pack", entity_id=pack_id)
    return {"ok": True, "id": pack_id, "name": name}


@router.post("/api/web/packs/{pack}/capture")
async def capture_pack_design(
    pack: str,
    session: AsyncSession = Depends(get_session),
    _identity=Depends(verify_identity_designer),
):
    """Снимает текущий сайт и сохраняет как дизайн существующего набора (встроенного дизайн-пака)."""
    if not _PACK_ID_RE.match(pack) or pack in {"default", "core"}:
        raise HTTPException(status_code=400, detail="Нельзя сохранять для этого набора")
    from database.web_default_seed import capture_and_store_pack_design

    try:
        site = await capture_and_store_pack_design(session, pack)
        await session.flush()
    except Exception as e:
        logger.exception("[capture_pack_design] упал: %s", e)
        raise HTTPException(status_code=500, detail=f"capture_pack_design: {type(e).__name__}: {e}")
    await _audit_web_admin(session, _identity, "design.capture_pack", entity_type="pack", entity_id=pack)
    pages_count = sum(1 for k, v in site.items() if not k.startswith("_") and isinstance(v, list))
    return {"ok": True, "pack": pack, "pages": pages_count}


@router.post("/api/web/blocks-pack/import")
async def import_blocks_pack(
    request: Request,
    session: AsyncSession = Depends(get_session),
    _identity=Depends(verify_identity_designer),
):
    """Добавляет новые блоки в конструктор из файла набора (бандл blueprints
    пользовательских элементов). Блоки сливаются в глобальную тему (страница landing)."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Некорректный файл набора (ожидается JSON)")
    if isinstance(body, list):
        blueprints = body
    elif isinstance(body, dict):
        blueprints = body.get("blueprints")
    else:
        blueprints = None
    if not isinstance(blueprints, list) or not blueprints:
        raise HTTPException(status_code=400, detail="Файл набора не содержит блоков (blueprints)")
    valid = [
        b
        for b in blueprints
        if isinstance(b, dict) and str(b.get("slug") or "").strip() and str(b.get("runtime") or "").strip()
    ]
    if not valid:
        raise HTTPException(status_code=400, detail="Некорректный формат блоков в файле набора")
    current, _ = await _resolve_variant(session, "landing", None)
    tokens = dict(current.theme_tokens or {})
    existing = tokens.get("customElementBlueprints")
    existing = existing if isinstance(existing, list) else []
    by_slug: dict = {}
    for b in existing:
        if isinstance(b, dict) and b.get("slug"):
            by_slug[str(b["slug"])] = b
    added = 0
    for b in valid:
        slug = str(b["slug"])
        if slug not in by_slug:
            added += 1
        by_slug[slug] = b
    tokens["customElementBlueprints"] = list(by_slug.values())
    cleaned_tokens, _replaced = migrate_json_data_uris(tokens)
    current.theme_tokens = cleaned_tokens
    await session.flush()
    await bump_site_revision(session)
    await _audit_web_admin(session, _identity, "blocks.import_pack", entity_type="site", entity_id="blueprints")
    return {"ok": True, "added": added, "total": len(by_slug)}


async def _merge_blueprints_into_landing(session: AsyncSession, blueprints: list) -> tuple[int, int]:
    valid = [
        b
        for b in blueprints
        if isinstance(b, dict) and str(b.get("slug") or "").strip() and str(b.get("runtime") or "").strip()
    ]
    current, _ = await _resolve_variant(session, "landing", None)
    tokens = dict(current.theme_tokens or {})
    existing = tokens.get("customElementBlueprints")
    existing = existing if isinstance(existing, list) else []
    by_slug: dict = {}
    for b in existing:
        if isinstance(b, dict) and b.get("slug"):
            by_slug[str(b["slug"])] = b
    added = 0
    for b in valid:
        slug = str(b["slug"])
        if slug not in by_slug:
            added += 1
        by_slug[slug] = b
    tokens["customElementBlueprints"] = list(by_slug.values())
    cleaned_tokens, _replaced = migrate_json_data_uris(tokens)
    current.theme_tokens = cleaned_tokens
    await session.flush()
    return added, len(by_slug)


@router.get("/api/web/packs/export-current.zip")
async def export_current_as_pack_zip(
    name: str = Query(default="Набор"),
    session: AsyncSession = Depends(get_session),
    _identity=Depends(verify_identity_designer),
):
    """Собирает текущий сайт в zip-набор: blocks.json (блоки) + design.json (установка дизайна) + meta.json."""
    import io
    import json as _json
    import zipfile

    from fastapi.responses import Response

    from database.web_default_seed import capture_current_site

    design = await capture_current_site(session)
    current, _ = await _resolve_variant(session, "landing", None)
    tokens = dict(current.theme_tokens or {})
    blueprints = tokens.get("customElementBlueprints")
    blueprints = blueprints if isinstance(blueprints, list) else []
    safe_name = str(name or "Набор")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "meta.json",
            _json.dumps({"name": safe_name, "version": 1, "kind": "solo-pack"}, ensure_ascii=False, indent=2),
        )
        zf.writestr("blocks.json", _json.dumps({"blueprints": blueprints}, ensure_ascii=False, indent=2))
        zf.writestr("design.json", _json.dumps(design, ensure_ascii=False, indent=2))
    buf.seek(0)
    fallback = "".join(c for c in safe_name if c.isalnum() or c in "-_") or "pack"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fallback}.solopack.zip"'},
    )


@router.post("/api/web/packs/import-zip")
async def import_pack_zip(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    _identity=Depends(verify_identity_designer),
):
    """Добавляет набор из zip-архива: blocks.json → блоки в конструктор, design.json → устанавливаемый дизайн."""
    import io
    import json as _json
    import zipfile

    from uuid import uuid4

    from database.web_default_seed import store_pack_design

    raw = await file.read()
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except Exception:
        raise HTTPException(status_code=400, detail="Файл не является zip-архивом набора")

    names = set(zf.namelist())

    def _read(cands: list[str]):
        for n in cands:
            if n in names:
                try:
                    return _json.loads(zf.read(n).decode("utf-8"))
                except Exception:
                    return None
        return None

    meta = _read(["meta.json", "pack.json"])
    meta = meta if isinstance(meta, dict) else {}
    blocks_doc = _read(["blocks.json", "blueprints.json"])
    design = _read(["design.json", "site.json", "install.json"])
    design = design if isinstance(design, dict) else {}

    if isinstance(blocks_doc, dict):
        blueprints = blocks_doc.get("blueprints")
    else:
        blueprints = blocks_doc
    blueprints = blueprints if isinstance(blueprints, list) else []

    base_name = (file.filename or "Набор").rsplit("/", 1)[-1]
    base_name = base_name.rsplit(".", 1)[0].replace(".solopack", "")
    name = str(meta.get("name") or base_name or "Импортированный набор").strip() or "Импортированный набор"
    description = str(meta.get("description") or "").strip()

    added_blocks = 0
    if blueprints:
        added_blocks, _total = await _merge_blueprints_into_landing(session, blueprints)

    pack_id = ""
    pages_count = 0
    has_design = any(not k.startswith("_") and isinstance(v, list) for k, v in design.items())
    if has_design:
        pack_id = "c" + uuid4().hex[:20]
        await store_pack_design(
            session, pack_id, design, meta={"name": name, "description": description, "custom": True}
        )
        pages_count = sum(1 for k, v in design.items() if not k.startswith("_") and isinstance(v, list))

    if not blueprints and not has_design:
        raise HTTPException(status_code=400, detail="В архиве нет ни blocks.json, ни design.json")

    await session.flush()
    await bump_site_revision(session)
    await _audit_web_admin(
        session, _identity, "design.import_pack_zip", entity_type="pack", entity_id=pack_id or "blocks"
    )
    return {"ok": True, "id": pack_id, "name": name, "addedBlocks": added_blocks, "pages": pages_count}


@router.post("/api/web/packs/import-file")
async def import_pack_file(
    request: Request,
    session: AsyncSession = Depends(get_session),
    _identity=Depends(verify_identity_designer),
):
    """Импорт расшариваемого набора из файла: добавляет блоки (blueprints) в конструктор
    и регистрирует дизайн как устанавливаемый свой набор (кнопка «Установить»)."""
    from uuid import uuid4

    from database.web_default_seed import store_pack_design

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Некорректный файл набора (ожидается JSON)")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Некорректный формат файла набора")
    meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
    name = str(meta.get("name") or "Импортированный набор").strip() or "Импортированный набор"
    description = str(meta.get("description") or "").strip()
    blueprints = body.get("blueprints") if isinstance(body.get("blueprints"), list) else []
    design = body.get("design") if isinstance(body.get("design"), dict) else {}

    added_blocks = 0
    if blueprints:
        added_blocks, _total = await _merge_blueprints_into_landing(session, blueprints)

    pack_id = ""
    pages_count = 0
    has_design = any(not k.startswith("_") and isinstance(v, list) for k, v in design.items())
    if has_design:
        pack_id = "c" + uuid4().hex[:20]
        await store_pack_design(
            session, pack_id, design, meta={"name": name, "description": description, "custom": True}
        )
        pages_count = sum(1 for k, v in design.items() if not k.startswith("_") and isinstance(v, list))

    if not blueprints and not has_design:
        raise HTTPException(status_code=400, detail="Файл набора пуст: нет ни блоков, ни дизайна")

    await session.flush()
    await bump_site_revision(session)
    await _audit_web_admin(
        session, _identity, "design.import_pack_file", entity_type="pack", entity_id=pack_id or "blocks"
    )
    return {"ok": True, "id": pack_id, "name": name, "addedBlocks": added_blocks, "pages": pages_count}


@router.delete("/api/web/packs/{pack}")
async def delete_custom_pack(
    pack: str,
    session: AsyncSession = Depends(get_session),
    _identity=Depends(verify_identity_designer),
):
    """Удаляет свой набор (встроенные удалять нельзя)."""
    if not _PACK_ID_RE.match(pack) or pack in _BUILTIN_PACK_IDS:
        raise HTTPException(status_code=400, detail="Нельзя удалить встроенный набор")
    from database.web_default_seed import delete_pack_design

    removed = await delete_pack_design(session, pack)
    await session.flush()
    if not removed:
        raise HTTPException(status_code=404, detail="Набор не найден")
    await _audit_web_admin(session, _identity, "design.delete_pack", entity_type="pack", entity_id=pack)
    return {"ok": True, "pack": pack}


@router.post("/api/web/packs/{pack}/install")
async def install_pack_design_endpoint(
    pack: str,
    session: AsyncSession = Depends(get_session),
    _identity=Depends(verify_identity_designer),
):
    """Устанавливает (восстанавливает) дизайн набора. Для «default» — встроенный seed."""
    if not _PACK_ID_RE.match(pack):
        raise HTTPException(status_code=400, detail="Некорректный id набора")
    try:
        if pack == "default":
            from database.web_default_seed import seed_default_site

            seeded = await seed_default_site(session, force=True)
        else:
            from database.web_default_seed import install_pack_design

            seeded = await install_pack_design(session, pack)
            if not seeded:
                raise HTTPException(status_code=404, detail="Для этого набора ещё не сохранён дизайн")
        await session.flush()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[install_pack_design] упал: %s", e)
        raise HTTPException(status_code=500, detail=f"install_pack_design: {type(e).__name__}: {e}")
    await bump_site_revision(session)
    await _audit_web_admin(session, _identity, "design.install_pack", entity_type="pack", entity_id=pack)
    return {"ok": True, "pack": pack, "seeded": seeded}


@router.get("/api/web/admin-audit")
async def web_admin_audit(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
    _identity=Depends(verify_identity_designer),
):
    """Журнал действий админов над сайтом (event_type=web_admin_action)."""
    from database.models import Identity
    from database.models.audit import AuditEvent

    base = AuditEvent.event_type == "web_admin_action"
    total = await session.scalar(select(func.count()).select_from(AuditEvent).where(base))
    rows = (
        (
            await session.execute(
                select(AuditEvent)
                .where(base)
                .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )

    ident_ids = {r.actor_identity_id for r in rows if r.actor_identity_id}
    emails: dict[str, str | None] = {}
    if ident_ids:
        eres = await session.execute(select(Identity.id, Identity.email).where(Identity.id.in_(ident_ids)))
        emails = dict(eres.all())

    items = [
        {
            "id": r.id,
            "action": r.path_or_handler,
            "entity_type": r.entity_type,
            "entity_id": r.entity_id,
            "result": r.result,
            "actor_email": emails.get(r.actor_identity_id),
            "actor_tg_id": r.actor_tg_id,
            "metadata": r.metadata_,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
    return {"total": int(total or 0), "items": items}


_BOT_LOG_PATH = Path("logs/logging.log")
_LEVEL_RANK = {"DEBUG": 0, "TRACE": 0, "INFO": 1, "SUCCESS": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}


def _tail_lines(path: Path, max_lines: int) -> list[str]:
    try:
        if not path.exists():
            return []
        size = path.stat().st_size
        chunk = min(size, 512 * 1024)
        with path.open("rb") as f:
            if chunk < size:
                f.seek(size - chunk)
            data = f.read()
        lines = data.decode("utf-8", errors="replace").splitlines()
        if chunk < size and lines:
            lines = lines[1:]
        return lines[-max_lines:]
    except Exception:
        return []


def _parse_log_line(raw: str) -> dict:
    parts = raw.split(" | ", 3)
    if len(parts) >= 3 and parts[1].strip().upper() in _LEVEL_RANK:
        ts = parts[0].strip()
        level = parts[1].strip().upper()
        loc = parts[2].strip() if len(parts) == 4 else ""
        msg = (parts[3] if len(parts) == 4 else parts[2]).strip()
        return {"ts": ts, "level": level, "loc": loc, "text": msg}
    up = raw.upper()
    level = "INFO"
    for token, mapped in (
        ("CRITICAL", "CRITICAL"),
        ("ERROR", "ERROR"),
        ("WARNING", "WARNING"),
        ("WARN", "WARNING"),
        ("DEBUG", "DEBUG"),
    ):
        if token in up:
            level = mapped
            break
    return {"ts": "", "level": level, "loc": "", "text": raw.strip()}


def _is_api_log(entry: dict) -> bool:
    return "[API]" in (entry.get("text") or "") or "log_api_access" in (entry.get("loc") or "")


def _site_log_token() -> str:
    try:
        from core.settings.web_config import WEB_CONFIG

        tok = str((WEB_CONFIG or {}).get("PLUGIN_BUILDER_TOKEN") or "").strip()
        if tok:
            return tok
    except Exception:
        pass
    try:
        from config import PLUGIN_BUILDER_TOKEN

        return str(PLUGIN_BUILDER_TOKEN or "").strip()
    except Exception:
        return ""


async def _fetch_site_log_lines(max_lines: int) -> tuple[list[str], bool, str | None]:
    from core.settings.web_config import get_site_url

    base = (get_site_url() or "").rstrip("/")
    token = _site_log_token()
    if not base:
        logger.warning("[logs] site-log: SITE_URL пуст (Настройки → Сайт), запрос не отправлен")
        return [], False, "SITE_URL не задан (Настройки → Сайт)."
    if not token:
        logger.warning("[logs] site-log: PLUGIN_BUILDER_TOKEN пуст (config.py / WEB_CONFIG), запрос не отправлен")
        return [], False, "PLUGIN_BUILDER_TOKEN не задан на боте (config.py / WEB_CONFIG)."
    import aiohttp

    url = f"{base}/api/internal/site-log?limit={max_lines}"
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.get(url, headers={"Authorization": f"Bearer {token}"}) as resp:
                if resp.status != 200:
                    body = (await resp.text())[:200]
                    logger.warning(
                        "[logs] site-log: {} вернул HTTP {} (токен len={}); ответ: {}",
                        url,
                        resp.status,
                        len(token),
                        body,
                    )
                    if resp.status in (401, 403):
                        return (
                            [],
                            False,
                            "Токен не подошёл: PLUGIN_BUILDER_TOKEN на боте и в веб-аппе различаются (или пуст в одном из них).",
                        )
                    return [], False, f"Веб-апп вернул HTTP {resp.status} на запрос логов сайта."
                data = await resp.json()
        if not isinstance(data, dict):
            return [], False, "Веб-апп вернул некорректный ответ."
        lines = data.get("lines") if isinstance(data.get("lines"), list) else []
        return [str(line) for line in lines], bool(data.get("available", True)), None
    except Exception as e:
        logger.warning("[logs] site-log: запрос к {} не удался: {}: {}", url, type(e).__name__, e)
        return [], False, f"Не удалось связаться с веб-аппом ({type(e).__name__}). Проверь SITE_URL."


def _api_logging_enabled() -> bool:
    try:
        from config import API_LOGGING

        return bool(API_LOGGING)
    except Exception:
        return True


@router.get("/api/web/logs")
@router.get("/logs")
async def get_logs(
    source: str = Query("bot"),
    limit: int = Query(200, ge=1, le=1000),
    level: str = Query("all"),
    _identity=Depends(verify_identity_designer),
):
    note: str | None = None
    if source == "site":
        raw_lines, available, note = await _fetch_site_log_lines(min(limit * 3, 6000))
    else:
        raw_lines = _tail_lines(_BOT_LOG_PATH, min(limit * 6, 6000))
        available = _BOT_LOG_PATH.exists()
    entries = [_parse_log_line(line) for line in raw_lines if line.strip()]
    if source == "api":
        entries = [e for e in entries if _is_api_log(e)]
        if not _api_logging_enabled():
            note = "API-логирование отключено в конфиге (API_LOGGING=False)."
    elif source == "bot":
        entries = [e for e in entries if not _is_api_log(e)]
    min_rank = {"warn": 2, "error": 3}.get(level, 0)
    if min_rank:
        entries = [e for e in entries if _LEVEL_RANK.get(e["level"], 1) >= min_rank]
    return {"source": source, "available": available, "entries": entries[-limit:], "note": note}


@router.get("/api/web/logs/health")
@router.get("/logs/health")
async def get_logs_health(_identity=Depends(verify_identity_designer)):
    out: dict = {}

    def _count(entries: list[dict]) -> dict:
        errors = warnings = 0
        for e in entries:
            rank = _LEVEL_RANK.get(e["level"], 1)
            if rank >= 3:
                errors += 1
            elif rank == 2:
                warnings += 1
        return {"errors": errors, "warnings": warnings, "lines": len(entries)}

    bot_lines = [_parse_log_line(line) for line in _tail_lines(_BOT_LOG_PATH, 500) if line.strip()]
    bot_available = _BOT_LOG_PATH.exists()
    out["api"] = {"available": bot_available, **_count([e for e in bot_lines if _is_api_log(e)])}
    out["bot"] = {"available": bot_available, **_count([e for e in bot_lines if not _is_api_log(e)])}

    site_raw, site_available, _site_note = await _fetch_site_log_lines(500)
    site_lines = [_parse_log_line(line) for line in site_raw if line.strip()]
    out["site"] = {"available": site_available, **_count(site_lines)}
    try:
        from utils.versioning import get_version

        out["botVersion"] = get_version(include_git_info=True)
    except Exception:
        out["botVersion"] = ""
    return out


@router.get("/api/web/node-status")
async def web_node_status(request: Request, session: AsyncSession = Depends(get_session)):
    """Статусы серверов для блока в кабинете — только серверы из тарифа юзера (его сквады
    в Remnawave). Гостю/без подписки отдаём пусто. host:port — для браузерной пробы пинга."""
    from api.depends import bind_identity_actor
    from api.v2.routes.keys._common import _resolve_billing_user_id, resolve_user_squad_uuids
    from services.remnawave_monitor import get_client_node_statuses

    identity = await _identity_from_cookie(session, request)
    if identity is None:
        return {"nodes": []}
    await bind_identity_actor(request, session, identity)
    billing_user_id = await _resolve_billing_user_id(request, identity, session)
    squads = await resolve_user_squad_uuids(session, billing_user_id)
    if not squads:
        return {"nodes": []}
    return {"nodes": await get_client_node_statuses(session, allowed_squad_uuids=squads)}


@router.get("/api/web/node-status/admin")
async def web_node_status_admin(
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_designer),
):
    """Полный список нод для редактора блока — только админ."""
    from services.remnawave_monitor import get_client_node_statuses

    return {"nodes": await get_client_node_statuses(session)}
