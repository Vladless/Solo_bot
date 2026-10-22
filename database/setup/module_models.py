import importlib

from pathlib import Path

from logger import logger
from utils.core_features import is_core_replaced_module


MODULES_DIR = Path(__file__).resolve().parents[2] / "modules"


def import_module_models() -> None:
    """Регистрирует модели модулей для обновления схемы."""
    if not MODULES_DIR.is_dir():
        return
    for module_path in sorted(MODULES_DIR.iterdir()):
        if is_core_replaced_module(module_path.name):
            continue
        if not (module_path / "models.py").is_file():
            continue
        try:
            importlib.import_module(f"modules.{module_path.name}.models")
        except Exception as exc:
            logger.warning(f"[Schema] модели модуля {module_path.name} не загружены: {exc}")
