from datetime import datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Key, Payment, Server, User
from filters.admin import IsAdminFilter
from logger import logger

from ..panel.keyboard import AdminPanelCallback, build_admin_back_kb
from .keyboard import AdminSenderCallback, build_clusters_kb, build_sender_kb

router = Router()


class AdminSender(StatesGroup):
    waiting_for_message = State()
    preview = State()


@router.callback_query(
    AdminPanelCallback.filter(F.action == "sender"),
    IsAdminFilter(),
)
async def handle_sender(callback_query: CallbackQuery):
    await callback_query.message.edit_text(
        text="✍️ Выберите группу пользователей для рассылки:",
        reply_markup=build_sender_kb(),
    )


@router.callback_query(
    AdminSenderCallback.filter(F.type != "cluster-select"),
    IsAdminFilter(),
)
async def handle_sender_callback_text(
    callback_query: CallbackQuery, callback_data: AdminSenderCallback, state: FSMContext
):
    await callback_query.message.edit_text(
        text=(
            "✍️ Введите текст сообщения для рассылки\n\n"
            "Поддерживается только Telegram-форматирование — <b>жирный</b>, <i>курсив</i> и другие стили через редактор Telegram.\n\n"
            "Вы можете отправить:\n"
            "• Только <b>текст</b>\n"
            "• Только <b>картинку</b>\n"
            "• <b>Текст + картинку</b>"
        ),
        reply_markup=build_admin_back_kb("sender"),
    )
    await state.update_data(type=callback_data.type, cluster_name=callback_data.data)
    await state.set_state(AdminSender.waiting_for_message)


@router.callback_query(
    AdminSenderCallback.filter(F.type == "cluster-select"),
    IsAdminFilter(),
)
async def handle_sender_callback(callback_query: CallbackQuery, session: AsyncSession):
    result = await session.execute(select(Server.cluster_name).distinct())
    clusters = result.mappings().all()

    await callback_query.message.answer(
        "✍️ Выберите кластер для рассылки сообщений:",
        reply_markup=build_clusters_kb(clusters),
    )


@router.message(AdminSender.waiting_for_message, IsAdminFilter())
async def handle_message_input(message: Message, state: FSMContext):
    text_message = message.html_text or message.text or message.caption or ""
    photo = message.photo[-1].file_id if message.photo else None

    max_len = 1024 if photo else 4096
    if len(text_message) > max_len:
        await message.answer(
            f"⚠️ Сообщение слишком длинное.\n"
            f"Максимум: <b>{max_len}</b> символов, сейчас: <b>{len(text_message)}</b>.",
            reply_markup=build_admin_back_kb("sender"),
        )
        await state.clear()
        return

    await state.update_data(text=text_message, photo=photo)
    await state.set_state(AdminSender.preview)

    if photo:
        await message.answer_photo(photo=photo, caption=text_message, parse_mode="HTML")
    else:
        await message.answer(text=text_message, parse_mode="HTML")

    await message.answer(
        "👀 Это предпросмотр рассылки.\nОтправить?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="📤 Отправить", callback_data="send_message"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_message"),
            ]
        ]),
    )


@router.callback_query(F.data == "send_message", IsAdminFilter())
async def handle_send_confirm(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    text_message = data.get("text")
    photo = data.get("photo")
    send_to = data.get("type", "all")
    cluster_name = data.get("cluster_name")
    now_ms = int(datetime.utcnow().timestamp() * 1000)

    query = None
    if send_to == "subscribed":
        query = select(distinct(User.tg_id)).join(Key).where(Key.expiry_time > now_ms)
    elif send_to == "unsubscribed":
        subquery = (
            select(User.tg_id)
            .outerjoin(Key, User.tg_id == Key.tg_id)
            .group_by(User.tg_id)
            .having(func.count(Key.tg_id) == 0)
            .union_all(
                select(User.tg_id)
                .join(Key, User.tg_id == Key.tg_id)
                .group_by(User.tg_id)
                .having(func.max(Key.expiry_time) <= now_ms)
            )
        )
        query = select(distinct(subquery.c.tg_id))
    elif send_to == "untrial":
        subquery = select(Key.tg_id)
        query = select(distinct(User.tg_id)).where(~User.tg_id.in_(subquery))
    elif send_to == "cluster":
        query = (
            select(distinct(User.tg_id))
            .join(Key, User.tg_id == Key.tg_id)
            .join(Server, Key.server_id == Server.cluster_name)
            .where(Server.cluster_name == cluster_name)
        )
    elif send_to == "hotleads":
        subquery = select(Key.tg_id)
        query = (
            select(distinct(User.tg_id))
            .join(Payment, User.tg_id == Payment.tg_id)
            .where(Payment.status == "success")
            .where(~User.tg_id.in_(subquery))
        )
    else:
        query = select(distinct(User.tg_id))

    result = await session.execute(query)
    tg_ids = [row[0] for row in result.all()]

    total_users = len(tg_ids)
    success_count = 0

    await callback_query.message.edit_text(
        f"📤 <b>Рассылка начата!</b>\n👥 Количество получателей: {total_users}"
    )

    for tg_id in tg_ids:
        try:
            if photo:
                await callback_query.bot.send_photo(
                    chat_id=tg_id,
                    photo=photo,
                    caption=text_message,
                    parse_mode="HTML",
                )
            else:
                await callback_query.bot.send_message(
                    chat_id=tg_id,
                    text=text_message,
                    parse_mode="HTML",
                )
            success_count += 1
        except Exception as e:
            logger.error(f"❌ Ошибка отправки пользователю {tg_id}: {e}")

    await callback_query.message.answer(
        text=(
            f"📤 <b>Рассылка завершена!</b>\n\n"
            f"👥 <b>Количество получателей:</b> {total_users}\n"
            f"✅ <b>Доставлено:</b> {success_count}\n"
            f"❌ <b>Не доставлено:</b> {total_users - success_count}"
        ),
        reply_markup=build_admin_back_kb("sender"),
    )
    await state.clear()


@router.callback_query(F.data == "cancel_message", IsAdminFilter())
async def handle_send_cancel(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.message.edit_text(
        "🚫 Рассылка отменена.",
        reply_markup=build_admin_back_kb("sender"),
    )
    await state.clear()