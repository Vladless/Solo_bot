from __future__ import annotations

import base64
import re
import uuid

from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


UPLOAD_DIR = Path("static/web_uploads")
DATA_URI_THRESHOLD_BYTES = 2048

_DATA_URI_RE = re.compile(r"^data:([\w./+-]+);base64,(.+)$", re.DOTALL)

_MIME_TO_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
}


def _save_data_uri_to_file(data_uri: str) -> str | None:
    match = _DATA_URI_RE.match(data_uri)
    if not match:
        return None
    mime = match.group(1).strip().lower()
    payload = match.group(2)
    ext = _MIME_TO_EXT.get(mime)
    if not ext:
        return None
    try:
        cleaned = "".join(payload.split())
        decoded = base64.b64decode(cleaned, validate=False)
    except Exception:
        return None
    if not decoded:
        return None
    if ext == ".svg":
        try:
            from api.v2.routes.web import _sanitize_svg

            decoded = _sanitize_svg(decoded)
        except Exception:
            return None
        if not decoded:
            return None
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}{ext}"
    (UPLOAD_DIR / name).write_bytes(decoded)
    return f"/api/web/uploads/{name}"


def migrate_json_data_uris(value: Any) -> tuple[Any, int]:
    replaced = 0

    def walk(node: Any) -> Any:
        nonlocal replaced
        if isinstance(node, str):
            if not node.startswith("data:"):
                return node
            if len(node) < DATA_URI_THRESHOLD_BYTES:
                return node
            url = _save_data_uri_to_file(node)
            if url is None:
                return node
            replaced += 1
            return url
        if isinstance(node, list):
            return [walk(item) for item in node]
        if isinstance(node, dict):
            return {key: walk(item) for key, item in node.items()}
        return node

    return walk(value), replaced


async def run_startup_data_uri_migration(session: AsyncSession) -> tuple[int, int]:
    from database.models import (
        WebBlock,
        WebPageVariant,
        WebPageVariantBlock,
        WebTheme,
    )

    rows_updated = 0
    uris_replaced = 0

    for theme in (await session.execute(select(WebTheme))).scalars().all():
        cleaned, replaced = migrate_json_data_uris(theme.tokens or {})
        if replaced:
            theme.tokens = cleaned
            rows_updated += 1
            uris_replaced += replaced

    for variant in (await session.execute(select(WebPageVariant))).scalars().all():
        cleaned, replaced = migrate_json_data_uris(variant.theme_tokens or {})
        if replaced:
            variant.theme_tokens = cleaned
            rows_updated += 1
            uris_replaced += replaced

    for block in (await session.execute(select(WebBlock))).scalars().all():
        cleaned, replaced = migrate_json_data_uris(block.data or {})
        if replaced:
            block.data = cleaned
            rows_updated += 1
            uris_replaced += replaced

    for block in (await session.execute(select(WebPageVariantBlock))).scalars().all():
        cleaned, replaced = migrate_json_data_uris(block.data or {})
        if replaced:
            block.data = cleaned
            rows_updated += 1
            uris_replaced += replaced

    return rows_updated, uris_replaced
