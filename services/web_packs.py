import json
import shutil
import zipfile

from dataclasses import dataclass
from pathlib import Path

from logger import logger


PACKS_DIR = Path("static/web_packs")
MANIFEST_NAME = "manifest.json"
PACK_ID_MAX_LEN = 64
PACK_DOWNLOAD_TIMEOUT_SEC = 30.0


@dataclass
class PackInstallResult:
    """Итог установки пака: что поставили и почему отказали."""

    ok: bool
    pack_id: str = ""
    version: str = ""
    error: str = ""


def packs_dir() -> Path:
    PACKS_DIR.mkdir(parents=True, exist_ok=True)
    return PACKS_DIR


def is_safe_pack_id(value: str) -> bool:
    """Идентификатор пака становится именем каталога — пускаем только безопасные."""
    raw = (value or "").strip()
    if not raw or len(raw) > PACK_ID_MAX_LEN:
        return False
    return all(ch.isalnum() or ch in "-_" for ch in raw)


def read_manifest(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("[WebPacks] Манифест {} не читается: {}", path, e)
        return None
    if not isinstance(data, dict):
        return None
    pack_id = str(data.get("id") or "").strip()
    entry = str(data.get("entry") or "").strip()
    elements = data.get("elements")
    seed = str(data.get("seed") or "").strip()
    has_blocks = bool(entry) and isinstance(elements, list) and bool(elements)
    if not is_safe_pack_id(pack_id) or not (has_blocks or seed):
        logger.warning("[WebPacks] Манифест {} неполный — пропускаю", path)
        return None
    return data


def list_installed_packs(base_url: str = "/api/web/packs/files") -> list[dict]:
    """Манифесты установленных паков со ссылкой на бандл в origin бота."""
    root = packs_dir()
    result: list[dict] = []
    for pack_path in sorted(root.iterdir()):
        if not pack_path.is_dir():
            continue
        manifest = read_manifest(pack_path / MANIFEST_NAME)
        if manifest is None:
            continue
        pack_id = str(manifest["id"]).strip()
        entry = str(manifest.get("entry") or "").strip().lstrip("/")
        if not entry:
            continue
        if not (pack_path / entry).exists():
            logger.warning("[WebPacks] Пак {}: бандл {} отсутствует — пропускаю", pack_id, entry)
            continue
        manifest["entry"] = f"{base_url.rstrip('/')}/{pack_id}/{entry}"
        result.append(manifest)
    return result


def installed_pack_version(pack_id: str) -> str:
    """Версия установленного пака из его манифеста. Пустая строка — пак не стоит."""
    if not is_safe_pack_id(pack_id):
        return ""
    manifest = read_manifest(packs_dir() / pack_id / MANIFEST_NAME)
    if manifest is None:
        return ""
    return str(manifest.get("version") or "").strip()


def load_pack_seed(pack_id: str) -> dict | None:
    """Дизайн набора из установленного пака: страницы, темы и flow приезжают вместе с блоками."""
    if not is_safe_pack_id(pack_id):
        return None
    manifest = read_manifest(packs_dir() / pack_id / MANIFEST_NAME)
    if manifest is None:
        return None
    seed_name = str(manifest.get("seed") or "seed.json").strip().lstrip("/")
    if not seed_name:
        return None
    seed_path = packs_dir() / pack_id / seed_name
    if not seed_path.exists():
        return None
    try:
        data = json.loads(seed_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("[WebPacks] Сид набора {} не читается: {}", pack_id, e)
        return None
    return data if isinstance(data, dict) else None


def has_pack_seed(pack_id: str) -> bool:
    return load_pack_seed(pack_id) is not None


def install_pack_from_zip(archive: Path) -> PackInstallResult:
    """Ставит пак из архива: манифест обязателен, чужие пути в архиве отсекаются."""
    if not archive.exists():
        return PackInstallResult(ok=False, error="архив не найден")

    try:
        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
            if MANIFEST_NAME not in names:
                return PackInstallResult(ok=False, error="в архиве нет manifest.json")
            for name in names:
                target = Path(name)
                if target.is_absolute() or ".." in target.parts:
                    return PackInstallResult(ok=False, error=f"недопустимый путь в архиве: {name}")

            manifest = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
            pack_id = str(manifest.get("id") or "").strip()
            if not is_safe_pack_id(pack_id):
                return PackInstallResult(ok=False, error="недопустимый id пака")
            entry = str(manifest.get("entry") or "").strip().lstrip("/")
            seed = str(manifest.get("seed") or "").strip().lstrip("/")
            if entry and entry not in names:
                return PackInstallResult(ok=False, error="бандл из манифеста отсутствует в архиве")
            if seed and seed not in names:
                return PackInstallResult(ok=False, error="сид из манифеста отсутствует в архиве")
            if not entry and not seed:
                return PackInstallResult(ok=False, error="в манифесте нет ни бандла, ни сида")

            destination = packs_dir() / pack_id
            if destination.exists():
                shutil.rmtree(destination)
            destination.mkdir(parents=True, exist_ok=True)
            zf.extractall(destination)
    except zipfile.BadZipFile:
        return PackInstallResult(ok=False, error="архив повреждён")
    except Exception as e:
        logger.error("[WebPacks] Установка из {} не удалась: {}", archive, e)
        return PackInstallResult(ok=False, error=str(e))

    version = str(manifest.get("version") or "").strip()
    logger.info("[WebPacks] Пак {} версии {} установлен", pack_id, version or "?")
    return PackInstallResult(ok=True, pack_id=pack_id, version=version)


async def download_and_install_pack(pack_id: str) -> PackInstallResult:
    """Ставит набор из источника. Архив попадает в каталог паков только после проверки."""
    import tempfile

    from core.rpc import fetch_pack_payload

    if not is_safe_pack_id(pack_id):
        return PackInstallResult(ok=False, error="недопустимый id пака")

    payload, error = await fetch_pack_payload(pack_id, timeout=PACK_DOWNLOAD_TIMEOUT_SEC)
    if payload is None:
        logger.warning("[WebPacks] Набор {} получить не удалось: {}", pack_id, error)
        return PackInstallResult(ok=False, pack_id=pack_id, error=error)

    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / f"{pack_id}.zip"
        archive.write_bytes(payload)
        result = install_pack_from_zip(archive)

    if result.ok and result.pack_id != pack_id:
        remove_pack(result.pack_id)
        return PackInstallResult(ok=False, pack_id=pack_id, error="id в манифесте не совпал с запрошенным")
    return result


def remove_pack(pack_id: str) -> bool:
    """Удаляет установленный пак вместе с файлами."""
    if not is_safe_pack_id(pack_id):
        return False
    destination = packs_dir() / pack_id.strip()
    if not destination.is_dir():
        return False
    shutil.rmtree(destination)
    logger.info("[WebPacks] Пак {} удалён", pack_id)
    return True
