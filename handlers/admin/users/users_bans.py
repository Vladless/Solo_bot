from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from handlers.buttons import BACK

from database.models import ManualBan
from filters.admin import IsAdminFilter

from .keyboard import AdminUserEditorCallback, build_editor_btn, build_editor_kb, build_user_ban_type_kb
from .users_states import BanUserStates


router = Router()


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_ban"),
    IsAdminFilter(),
)
async def handle_user_ban(callback: CallbackQuery, callback_data: AdminUserEditorCallback, state: FSMContext):
    await state.clear()
    await state.update_data(tg_id=callback_data.tg_id)

    await callback.message.edit_text(
        text="🚫 Выберите тип блокировки пользователя:",
        reply_markup=build_user_ban_type_kb(callback_data.tg_id),
    )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_ban_forever"),
    IsAdminFilter(),
)
async def handle_ban_forever_start(callback: CallbackQuery, callback_data: AdminUserEditorCallback, state: FSMContext):
    await state.set_state(BanUserStates.waiting_for_forever_reason)
    await state.update_data(tg_id=callback_data.tg_id)

    kb = InlineKeyboardBuilder()
    kb.row(build_editor_btn(BACK, tg_id=callback_data.tg_id, edit=True))

    await callback.message.edit_text(
        text="✏️ Введите причину <b>постоянной блокировки</b> (или <code>-</code>, чтобы пропустить):",
        reply_markup=kb.as_markup(),
    )


@router.message(BanUserStates.waiting_for_forever_reason, IsAdminFilter())
async def handle_ban_forever_reason_input(message: Message, state: FSMContext, session: AsyncSession):
    reason = message.text.strip()
    if reason == "-":
        reason = None

    user_data = await state.get_data()
    tg_id = user_data.get("tg_id")

    stmt = (
        pg_insert(ManualBan)
        .values(
            tg_id=tg_id,
            reason=reason,
            banned_by=message.from_user.id,
            until=None,
            banned_at=datetime.now(timezone.utc),
        )
        .on_conflict_do_update(
            index_elements=[ManualBan.tg_id],
            set_={
                "reason": reason,
                "until": None,
                "banned_by": message.from_user.id,
                "banned_at": datetime.now(timezone.utc),
            },
        )
    )

    await session.execute(stmt)
    await session.commit()
    await state.clear()

    await message.answer(
        text=(f"✅ Пользователь <code>{tg_id}</code> забанен навсегда.{f'\n📄 Причина: {reason}' if reason else ''}"),
        reply_markup=build_editor_kb(tg_id, edit=True),
    )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_ban_temporary"),
    IsAdminFilter(),
)
async def handle_ban_temporary(callback: CallbackQuery, callback_data: AdminUserEditorCallback, state: FSMContext):
    await state.set_state(BanUserStates.waiting_for_reason)
    await state.update_data(tg_id=callback_data.tg_id)

    kb = InlineKeyboardBuilder()
    kb.row(build_editor_btn(BACK, tg_id=callback_data.tg_id, edit=True))

    await callback.message.edit_text(
        text="✏️ Введите причину <b>временной блокировки</b> (или <code>-</code>, чтобы пропустить):",
        reply_markup=kb.as_markup(),
    )


@router.message(BanUserStates.waiting_for_reason, IsAdminFilter())
async def handle_ban_reason_input(message: Message, state: FSMContext):
    await state.update_data(reason=message.text.strip())
    await state.set_state(BanUserStates.waiting_for_ban_duration)

    user_data = await state.get_data()
    tg_id = user_data.get("tg_id")

    kb = InlineKeyboardBuilder()
    kb.row(build_editor_btn(BACK, tg_id=tg_id, edit=True))

    await message.answer(
        "⏳ Введите срок блокировки в днях (0 — навсегда):",
        reply_markup=kb.as_markup(),
    )


@router.message(BanUserStates.waiting_for_ban_duration, IsAdminFilter())
async def handle_ban_duration_input(message: Message, state: FSMContext, session: AsyncSession):
    user_data = await state.get_data()
    tg_id = user_data.get("tg_id")
    reason = user_data.get("reason")
    if reason == "-":
        reason = None

    try:
        days = int(message.text.strip())
        if days < 1:
            await message.answer("❗ Укажите срок минимум в 1 день.")
            return

        until = datetime.now(timezone.utc) + timedelta(days=days)

        stmt = (
            pg_insert(ManualBan)
            .values(
                tg_id=tg_id,
                reason=reason,
                banned_by=message.from_user.id,
                until=until,
                banned_at=datetime.now(timezone.utc),
            )
            .on_conflict_do_update(
                index_elements=[ManualBan.tg_id],
                set_={
                    "reason": reason,
                    "until": until,
                    "banned_at": datetime.now(timezone.utc),
                    "banned_by": message.from_user.id,
                },
            )
        )

        await session.execute(stmt)
        await session.commit()

        text = (
            f"✅ Пользователь <code>{tg_id}</code> временно забанен до <b>{until:%Y-%m-%d %H:%M}</b> по UTC."
            f"{f'\n📄 Причина: {reason}' if reason else ''}"
        )

        await message.answer(text=text, reply_markup=build_editor_kb(tg_id, edit=True))
    except ValueError:
        await message.answer("❗ Введите корректное число дней.")
    finally:
        await state.clear()


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_ban_shadow"),
    IsAdminFilter(),
)
async def handle_ban_shadow(callback: CallbackQuery, callback_data: AdminUserEditorCallback, session: AsyncSession):
    stmt = (
        pg_insert(ManualBan)
        .values(
            tg_id=callback_data.tg_id,
            reason="shadow",
            banned_by=callback.from_user.id,
            until=None,
            banned_at=datetime.now(timezone.utc),
        )
        .on_conflict_do_update(
            index_elements=[ManualBan.tg_id],
            set_={
                "reason": "shadow",
                "until": None,
                "banned_by": callback.from_user.id,
                "banned_at": datetime.now(timezone.utc),
            },
        )
    )
    await session.execute(stmt)
    await session.commit()

    await callback.message.edit_text(
        text=f"👻 Пользователь <code>{callback_data.tg_id}</code> получил теневой бан.",
        reply_markup=build_editor_kb(callback_data.tg_id, edit=True),
    )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_unban"),
    IsAdminFilter(),
)
async def handle_user_unban(
    callback: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    await session.execute(delete(ManualBan).where(ManualBan.tg_id == callback_data.tg_id))
    await session.commit()

    text = (
        f"✅ Пользователь <code>{callback_data.tg_id}</code> разблокирован. Нажмите кнопку ниже для возврата в профиль."
    )

    await callback.message.edit_text(text=text, reply_markup=build_editor_kb(callback_data.tg_id, edit=True))
