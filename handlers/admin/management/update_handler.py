import asyncio
import json
import os
import re
import shutil
import subprocess
import sys

import aiohttp

from aiogram import F
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.executor import run_io, spawn
from filters.admin import HasPermission, IsAdminFilter
from filters.permissions import PERM_MANAGEMENT
from logger import logger

from ..panel.headers import menu_text, quote, section
from ..panel.keyboard import AdminPanelCallback, build_admin_back_btn
from . import router


PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
LAUNCHER = os.path.join(PROJECT_DIR, "cli_launcher.py")
REPORT_FILE = os.path.join(PROJECT_DIR, ".update_report.json")

RELEASES_URL = "https://api.github.com/repos/Vladless/Solo_bot/releases"
TAGS_URL = "https://api.github.com/repos/Vladless/Solo_bot/tags"
MIN_MAJOR = 4
TAGS_SHOWN = 8

FILE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("img", "Картинки"),
    ("buttons", "Кнопки"),
    ("redis_cache", "Redis"),
)
FILE_KEYS = frozenset(key for key, _ in FILE_OPTIONS)

_state: dict[int, dict] = {}


def _admin_state(admin_id: int) -> dict:
    return _state.setdefault(admin_id, {"channel": "release", "tag": "", "overwrite": {}})


def _installed_version() -> str:
    from core.rpc import local_version

    return local_version(PROJECT_DIR) or "неизвестна"


def parse_tag_version(tag: str) -> tuple[int, ...]:
    """Кортеж чисел из тега для сортировки: v.5.1 → (5, 1)."""
    parts: list[int] = []
    for part in re.split(r"[.\s]+", tag.strip().lstrip("v.")):
        try:
            parts.append(int(part))
        except ValueError:
            break
    return tuple(parts) if parts else (0,)


async def fetch_tags() -> list[tuple[str, bool]]:
    """Теги от старых к новым: (имя, это релиз). Пустой список — GitHub недоступен."""
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(TAGS_URL, params={"per_page": 50}) as response:
            if response.status != 200:
                return []
            tags = await response.json()
        release_names: set[str] = set()
        async with session.get(RELEASES_URL) as response:
            if response.status == 200:
                release_names = {item.get("tag_name") for item in await response.json()}

    names = [str(item.get("name") or "") for item in tags]
    names = [name for name in names if name and parse_tag_version(name)[0] >= MIN_MAJOR]
    names.sort(key=parse_tag_version)
    return [(name, name in release_names) for name in names]


def _build_kb(state: dict) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    channel = state.get("channel", "release")

    builder.row(
        InlineKeyboardButton(
            text=f"{'🟢' if channel == 'release' else '⚪️'} Релиз",
            callback_data=AdminPanelCallback(action="update_channel_release").pack(),
        ),
        InlineKeyboardButton(
            text=f"{'🟡' if channel == 'beta' else '⚪️'} Бета",
            callback_data=AdminPanelCallback(action="update_channel_beta").pack(),
        ),
    )

    if channel == "release":
        builder.row(
            InlineKeyboardButton(
                text=f"🏷 Версия: {state.get('tag') or 'последняя'}",
                callback_data=AdminPanelCallback(action="update_tags").pack(),
            )
        )

    overwrite = state.get("overwrite") or {}
    for key, title in FILE_OPTIONS:
        builder.row(
            InlineKeyboardButton(
                text=f"{'♻️' if overwrite.get(key) else '🔒'} {title}",
                callback_data=AdminPanelCallback(action=f"update_toggle_{key}").pack(),
            )
        )

    builder.row(
        InlineKeyboardButton(
            text="⬇️ Обновить",
            callback_data=AdminPanelCallback(action="update_start").pack(),
        )
    )
    builder.row(build_admin_back_btn("management"))
    return builder


def _screen_text(state: dict, markup=None) -> str:
    channel = state.get("channel", "release")
    overwrite = state.get("overwrite") or {}

    lines = [f"Канал: {'релиз' if channel == 'release' else 'бета'}"]
    if channel == "release":
        lines.append(f"Версия: {state.get('tag') or 'последняя'}")
    for key, title in FILE_OPTIONS:
        lines.append(f"{title}: {'♻️ перезапишем' if overwrite.get(key) else '🔒 сохраним'}")

    return menu_text(
        "Обновление",
        f"Установлено: <b>{_installed_version()}</b>",
        section("⚙️ Что ставим", *lines),
        section(
            "⚠️ Как это работает",
            "папка бота перезаписывается версией из выбранного канала",
            "перед этим создаётся бэкап, при сбое откат автоматический",
            "бот выключится на время обновления и пришлёт отчёт после старта",
        ),
        markup=markup,
    )


async def _render(callback: CallbackQuery) -> None:
    state = _admin_state(callback.from_user.id)
    markup = _build_kb(state).as_markup()
    await callback.message.edit_text(text=_screen_text(state, markup), reply_markup=markup)
    await callback.answer()


@router.callback_query(
    AdminPanelCallback.filter(F.action == "update"),
    HasPermission(PERM_MANAGEMENT),
    IsAdminFilter(),
)
async def open_update(callback: CallbackQuery) -> None:
    await _render(callback)


@router.callback_query(
    AdminPanelCallback.filter(F.action.in_({"update_channel_release", "update_channel_beta"})),
    HasPermission(PERM_MANAGEMENT),
)
async def switch_channel(callback: CallbackQuery, callback_data: AdminPanelCallback) -> None:
    state = _admin_state(callback.from_user.id)
    state["channel"] = "beta" if callback_data.action.endswith("beta") else "release"
    await _render(callback)


@router.callback_query(AdminPanelCallback.filter(F.action == "update_tags"), HasPermission(PERM_MANAGEMENT))
async def choose_tag(callback: CallbackQuery) -> None:
    try:
        tags = await fetch_tags()
    except Exception as error:
        logger.error("[Update] Не удалось получить список версий: {}", error)
        tags = []

    if not tags:
        await callback.answer("GitHub недоступен, список версий не получен", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🆕 Последняя доступная",
            callback_data=AdminPanelCallback(action="update_tag|").pack(),
        )
    )
    for name, is_release in reversed(tags[-TAGS_SHOWN:]):
        builder.row(
            InlineKeyboardButton(
                text=f"{'🟢' if is_release else '🩹'} {name}",
                callback_data=AdminPanelCallback(action=f"update_tag|{name}").pack(),
            )
        )
    builder.row(build_admin_back_btn("update"))

    markup = builder.as_markup()
    await callback.message.edit_text(
        text=menu_text(
            "Версии",
            f"Установлено: <b>{_installed_version()}</b>",
            quote("🟢 — релиз, 🩹 — патч."),
            markup=markup,
        ),
        reply_markup=markup,
    )
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action.startswith("update_tag|")), HasPermission(PERM_MANAGEMENT))
async def set_tag(callback: CallbackQuery, callback_data: AdminPanelCallback) -> None:
    state = _admin_state(callback.from_user.id)
    state["tag"] = callback_data.action.split("|", 1)[1]
    await _render(callback)


@router.callback_query(
    AdminPanelCallback.filter(F.action.startswith("update_toggle_")),
    HasPermission(PERM_MANAGEMENT),
)
async def toggle_file(callback: CallbackQuery, callback_data: AdminPanelCallback) -> None:
    key = callback_data.action.removeprefix("update_toggle_")
    if key not in FILE_KEYS:
        await callback.answer("Неизвестный пункт", show_alert=True)
        return
    overwrite = _admin_state(callback.from_user.id).setdefault("overwrite", {})
    overwrite[key] = not overwrite.get(key, False)
    await _render(callback)


def build_update_command(state: dict, notify: int) -> list[str]:
    """Аргументы неинтерактивного запуска CLI: канал, версия и что разрешено перезаписать."""
    command = [sys.executable, LAUNCHER, "--update", state.get("channel", "release")]
    tag = str(state.get("tag") or "").strip()
    if tag and state.get("channel", "release") == "release":
        command += ["--tag", tag]
    for key, _title in FILE_OPTIONS:
        if (state.get("overwrite") or {}).get(key):
            command.append(f"--with-{key.replace('_', '-')}")
    command += ["--notify", str(notify)]
    return command


UPDATE_UNIT = "solobot-update"


def _systemd_command(command: list[str]) -> list[str] | None:
    """Обновлятель в отдельном юните: systemd гасит весь cgroup службы вместе с ботом."""
    runner = shutil.which("systemd-run")
    if not runner:
        return None
    prefix = [
        runner,
        "--unit",
        UPDATE_UNIT,
        "--collect",
        "--service-type=oneshot",
        "--working-directory",
        PROJECT_DIR,
    ]
    if os.geteuid() != 0:
        prefix = ["sudo", "-n", *prefix]
    return prefix + command


def _launch_detached(command: list[str]) -> None:
    unit_command = _systemd_command(command)
    if unit_command is not None:
        result = subprocess.run(
            unit_command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            timeout=30,
        )
        if result.returncode == 0:
            return
        logger.warning("[Update] systemd-run не сработал: {}", result.stderr.decode(errors="replace").strip())

    subprocess.Popen(
        command,
        cwd=PROJECT_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


@router.callback_query(
    AdminPanelCallback.filter(F.action == "update_start"),
    HasPermission(PERM_MANAGEMENT),
    IsAdminFilter(),
)
async def start_update(callback: CallbackQuery) -> None:
    if not os.path.isfile(LAUNCHER):
        await callback.answer("Установщик не найден рядом с ботом", show_alert=True)
        return

    state = _admin_state(callback.from_user.id)
    try:
        os.unlink(REPORT_FILE)
    except OSError:
        pass

    kb = InlineKeyboardBuilder()
    kb.row(build_admin_back_btn("management"))
    markup = kb.as_markup()
    await callback.message.edit_text(
        text=menu_text(
            "Обновление",
            "⏳ Обновление запущено.",
            quote("Бот сейчас выключится — это нормально.\nКогда он поднимется, пришлю отчёт сюда же."),
            markup=markup,
        ),
        reply_markup=markup,
    )
    await callback.answer()

    command = build_update_command(state, callback.from_user.id)
    logger.info("[Update] Запуск обновления: {}", " ".join(command[2:]))
    spawn(_run_update(command))


async def _run_update(command: list[str]) -> None:
    await asyncio.sleep(1)
    try:
        await run_io(lambda: _launch_detached(command))
    except Exception as error:
        logger.error("[Update] Не удалось запустить обновление: {}", error)


def take_update_report() -> dict | None:
    """Отчёт обновлятеля. Читается один раз — потом файл удаляется."""
    try:
        with open(REPORT_FILE, encoding="utf-8") as fh:
            report = json.load(fh)
    except (OSError, ValueError):
        return None
    try:
        os.unlink(REPORT_FILE)
    except OSError:
        pass
    return report if isinstance(report, dict) else None


def format_report(report: dict) -> str:
    status = str(report.get("status") or "")
    detail = str(report.get("detail") or "")
    channel = "бета" if report.get("channel") == "beta" else "релиз"
    title = {
        "ok": "✅ Обновление завершено",
        "skipped": "⚠️ Обновление отменено",
    }.get(status, "❌ Обновление не удалось")
    return menu_text("Обновление", title, quote(f"Канал: {channel}\n{detail}"))


REPORT_WAIT_SECONDS = 300
REPORT_POLL_SECONDS = 5


async def report_update_result(bot) -> None:
    """Доклад админу после обновления.

    CLI дожидается старта бота и только потом пишет отчёт, поэтому на момент
    запуска файла ещё нет — ждём его появления в фоне.
    """
    spawn(_watch_report(bot))


async def _watch_report(bot) -> None:
    deadline = REPORT_WAIT_SECONDS
    while deadline > 0:
        report = take_update_report()
        if report is not None:
            admin_id = int(report.get("notify") or 0)
            if not admin_id:
                return
            try:
                await bot.send_message(admin_id, format_report(report))
            except Exception as error:
                logger.error("[Update] Не удалось отправить отчёт админу {}: {}", admin_id, error)
            return
        await asyncio.sleep(REPORT_POLL_SECONDS)
        deadline -= REPORT_POLL_SECONDS
