from urllib.parse import urlsplit

from .types import InstallError


def _panel_url(value: object) -> str:
    """Проверяет публичную ссылку панели без встроенных доступов."""
    if not isinstance(value, str):
        raise ValueError("URL панели должен быть текстом")
    value = value.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if (
            not value
            or len(value) > 255
            or any(character.isspace() or ord(character) < 32 for character in value)
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or (port is not None and port <= 0)
        ):
            raise ValueError
    except ValueError as error:
        raise ValueError("Некорректный URL панели Remnawave") from error
    return value


def resolve_install_defaults(panels: list[dict], configured_panel: str = "") -> dict:
    """Определяет единственную панель без выбора в установщике."""
    try:
        urls = {_panel_url(panel.get("panel_ref")) for panel in panels}
    except (ValueError, TypeError, AttributeError) as error:
        raise InstallError(
            "panel", "В настройках серверов задан некорректный URL панели Remnawave", retryable=False
        ) from error
    if not urls:
        raise InstallError(
            "panel", "Сначала добавьте настроенную панель Remnawave в раздел серверов бота", retryable=False
        )
    if not isinstance(configured_panel, str):
        raise InstallError("panel", "Проверьте REMNAWAVE_INSTALLER_PANEL в settings/config.py", retryable=False)
    if configured_panel.strip():
        try:
            selected = _panel_url(configured_panel)
        except ValueError as error:
            raise InstallError(
                "panel", "Проверьте URL REMNAWAVE_INSTALLER_PANEL в settings/config.py", retryable=False
            ) from error
        if selected not in urls:
            raise InstallError(
                "panel", "Панель REMNAWAVE_INSTALLER_PANEL не найдена среди настроенных серверов бота", retryable=False
            )
    elif len(urls) == 1:
        selected = urls.pop()
    else:
        raise InstallError(
            "panel",
            "Настроено несколько панелей Remnawave. Один раз укажите нужный URL в REMNAWAVE_INSTALLER_PANEL в settings/config.py",
            retryable=False,
        )
    return {"panel_ref": selected, "panel_sources": []}


def _configured_panel_ref() -> str:
    """Читает только несекретный адрес панели установщика."""
    from settings import config

    return getattr(config, "REMNAWAVE_INSTALLER_PANEL", "")


async def get_install_defaults() -> dict:
    """Возвращает безопасные параметры уже настроенной панели."""
    from .panel import list_panel_options

    return resolve_install_defaults(await list_panel_options(), _configured_panel_ref())
