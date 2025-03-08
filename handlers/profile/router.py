import html
import os

from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineQuery, InlineQueryResultArticle, InputTextMessageContent, Message
from config import INLINE_MODE, REFERRAL_OFFERS, TRIAL_TIME, USERNAME_BOT

from database import get_balance, get_key_count, get_last_payments, get_referral_stats, get_trial
from handlers.texts import get_referral_link, invite_message_send, profile_message_send
from handlers.utils import edit_or_send_message
from keyboards.profile import get_balance_keyboard, get_invite_keyboard, get_profile_keyboard


router = Router()


@router.callback_query(F.data == "profile")
@router.message(F.text == "/profile")
async def process_callback_view_profile(
    state: FSMContext,
    admin: bool,
    chat_id: int,
    target_message: Message,
):
    """
    Обрабатывает запрос на просмотр профиля пользователя.

    Args:
        state: Контекст состояния FSM.
        admin: Флаг, указывающий, является ли пользователь администратором.
        chat_id: ID чата (добавлено middleware).
        target_message: Целевое сообщение для ответа (добавлено middleware).
    """
    # Получаем информацию о профиле
    profile_message = await profile_message_send(chat_id)

    # Получаем клавиатуру профиля
    builder = get_profile_keyboard(admin)

    # Отправляем сообщение с профилем
    await edit_or_send_message(
        target_message=target_message,
        text=profile_message,
        reply_markup=builder.as_markup(),
        media_path=None,
    )


@router.callback_query(F.data == "balance")
async def balance_handler(callback_query: CallbackQuery, session: Any, chat_id: int, target_message: Message):
    """
    Обрабатывает запрос на просмотр баланса.

    Args:
        callback_query: Колбэк запрос.
        session: Сессия базы данных.
        chat_id: ID чата (добавлено middleware).
        target_message: Целевое сообщение для ответа (добавлено middleware).
    """
    balance = await get_balance(chat_id, session)

    # Получаем клавиатуру баланса
    builder = get_balance_keyboard()

    # Отправляем сообщение с балансом
    await edit_or_send_message(
        target_message=target_message,
        text=f"<b>💰 Ваш текущий баланс:</b> {balance} руб.\n\n"
        "Вы можете пополнить баланс через раздел <b>💸 Пополнить баланс</b> в личном кабинете.",
        reply_markup=builder.as_markup(),
        media_path=None,
    )


@router.callback_query(F.data == "balance_history")
async def balance_history_handler(callback_query: CallbackQuery, session: Any, chat_id: int, target_message: Message):
    """
    Обрабатывает запрос на просмотр истории баланса.

    Args:
        callback_query: Колбэк запрос.
        session: Сессия базы данных.
        chat_id: ID чата (добавлено middleware).
        target_message: Целевое сообщение для ответа (добавлено middleware).
    """
    payments = await get_last_payments(chat_id, session)

    if not payments:
        history_text = "<b>📊 История операций:</b>\n\nУ вас пока нет операций по балансу."
    else:
        history_text = "<b>📊 История операций:</b>\n\n"
        for payment in payments:
            amount = payment["amount"]
            date = payment["created_at"].strftime("%d.%m.%Y %H:%M")
            description = html.escape(payment["description"] or "")

            if amount > 0:
                history_text += f"➕ <b>{amount}</b> руб. - {description} ({date})\n"
            else:
                history_text += f"➖ <b>{abs(amount)}</b> руб. - {description} ({date})\n"

    # Получаем клавиатуру баланса
    builder = get_balance_keyboard()

    # Отправляем сообщение с историей баланса
    await edit_or_send_message(
        target_message=target_message,
        text=history_text,
        reply_markup=builder.as_markup(),
        media_path=None,
    )


@router.callback_query(F.data == "invite")
@router.message(F.text == "/invite")
async def invite_handler(chat_id: int, target_message: Message):
    """
    Обрабатывает запрос на приглашение друзей.

    Args:
        chat_id: ID чата (добавлено middleware).
        target_message: Целевое сообщение для ответа (добавлено middleware).
    """
    referral_link = get_referral_link(chat_id)
    referral_stats = await get_referral_stats(chat_id)
    invite_message = invite_message_send(referral_link, referral_stats)
    image_path = os.path.join("img", "pic_invite.jpg")

    # Получаем клавиатуру для приглашений
    builder = get_invite_keyboard(chat_id, referral_link)

    # Отправляем сообщение с приглашением
    await edit_or_send_message(
        target_message=target_message,
        text=invite_message,
        reply_markup=builder.as_markup(),
        media_path=image_path,
        disable_web_page_preview=False,
    )


@router.inline_query(F.query.in_(["referral", "ref", "invite"]))
async def inline_referral_handler(inline_query: InlineQuery):
    """
    Обрабатывает инлайн-запрос для реферальной программы.

    Args:
        inline_query: Инлайн-запрос.
    """

    results = []

    for index, offer in enumerate(REFERRAL_OFFERS):
        description = offer["description"][:64]
        message_text = offer["message"].format(trial_time=TRIAL_TIME)[:4096]

        results.append(
            InlineQueryResultArticle(
                id=f"ref_{index}",
                title=offer["title"],
                description=description,
                input_message_content=InputTextMessageContent(
                    message_text=message_text,
                    parse_mode="HTML",
                ),
                thumbnail_url=offer.get("thumbnail_url"),
                thumbnail_width=100,
                thumbnail_height=100,
            )
        )

    await inline_query.answer(results=results, cache_time=300)
