import importlib

from pathlib import Path

from logger import logger


MODULES_DIR = Path(__file__).resolve().parents[2] / "modules"


def import_module_models() -> None:
    """Регистрирует модели модулей в общей метадате: без импорта их таблиц в схеме не будет.

    Путь берётся от файла, а не от рабочего каталога: схему поднимают и из бота, и из CLI.
    """
    if not MODULES_DIR.is_dir():
        return
    for module_path in sorted(MODULES_DIR.iterdir()):
        if not (module_path / "models.py").is_file():
            continue
        try:
            importlib.import_module(f"modules.{module_path.name}.models")
        except Exception as exc:
            logger.warning(f"[Schema] модели модуля {module_path.name} не загружены: {exc}")
