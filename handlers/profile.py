import html
from io import BytesIO
import os

import asyncpg
import qrcode

from typing import Any

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import (
    ADMIN_ID,
    DATABASE_URL,
    GIFT_BUTTON,
    INLINE_MODE,
    INSTRUCTIONS_BUTTON,
    NEWS_MESSAGE,
    REFERRAL_BUTTON,
    REFERRAL_OFFERS,
    SHOW_START_MENU_ONCE,
    TOP_REFERRAL_BUTTON,
    TRIAL_TIME,
    USERNAME_BOT,
)
from database import get_balance, get_key_count, get_last_payments, get_referral_stats, get_trial
from handlers.buttons import (
    ABOUT_VPN,
    ADD_SUB,
    BACK,
    BALANCE,
    BALANCE_HISTORY,
    COUPON,
    GIFTS,
    INSTRUCTIONS,
    INVITE,
    MAIN_MENU,
    MY_SUBS,
    PAYMENT,
    QR,
    TOP_FIVE,
    TRIAL_SUB
)
from handlers.texts import BALANCE_HISTORY_HEADER, BALANCE_MANAGEMENT_TEXT, INVITE_TEXT_NON_INLINE, TOP_REFERRALS_TEXT
from logger import logger

from .admin.panel.keyboard import AdminPanelCallback
from .texts import get_referral_link, invite_message_send, profile_message_send
from .utils import edit_or_send_message, format_days


router = Router()


@router.callback_query(F.data == "profile")
@router.message(F.text == "/profile")
async def process_callback_view_profile(
    callback_query_or_message: Message | CallbackQuery,
    state: FSMContext,
    admin: bool,
):
    if isinstance(callback_query_or_message, CallbackQuery):
        chat = callback_query_or_message.message.chat
        from_user = callback_query_or_message.from_user
        chat_id = chat.id
        target_message = callback_query_or_message.message
    else:
        chat = callback_query_or_message.chat
        from_user = callback_query_or_message.from_user
        chat_id = chat.id
        target_message = callback_query_or_message

    user = chat if chat.type == "private" else from_user

    if getattr(user, "full_name", None):
        username = html.escape(user.full_name)
    elif getattr(user, "first_name", None):
        username = html.escape(user.first_name)
    elif getattr(user, "username", None):
        username = "@" + html.escape(user.username)
    else:
        username = "Пользователь"

    image_path = os.path.join("img", "profile.jpg")
    logger.info(f"Переход в профиль. Используется изображение: {image_path}")

    key_count = await get_key_count(chat_id)
    balance = await get_balance(chat_id) or 0

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        trial_status = await get_trial(chat_id, conn)

        profile_message = profile_message_send(username, chat_id, int(balance), key_count)
        if key_count == 0:
            profile_message += (
                "\n<blockquote>🔧 <i>Нажмите кнопку ➕ Подписка, чтобы настроить VPN-подключение</i></blockquote>"
            )
        else:
            profile_message += f"\n<blockquote> <i>{NEWS_MESSAGE}</i></blockquote>"

        builder = InlineKeyboardBuilder()
        if key_count > 0:
            builder.row(InlineKeyboardButton(text=MY_SUBS, callback_data="view_keys"))
        elif trial_status == 0:
            builder.row(InlineKeyboardButton(text=TRIAL_SUB, callback_data="create_key"))
        else:
            builder.row(InlineKeyboardButton(text=ADD_SUB, callback_data="create_key"))
        builder.row(InlineKeyboardButton(text=BALANCE, callback_data="balance"))

        row_buttons = []
        if REFERRAL_BUTTON:
            row_buttons.append(InlineKeyboardButton(text=INVITE, callback_data="invite"))
        if GIFT_BUTTON:
            row_buttons.append(InlineKeyboardButton(text=GIFTS, callback_data="gifts"))
        if row_buttons:
            builder.row(*row_buttons)

        if INSTRUCTIONS_BUTTON:
            builder.row(InlineKeyboardButton(text=INSTRUCTIONS, callback_data="instructions"))
        if admin:
            builder.row(
                InlineKeyboardButton(text="📊 Администратор", callback_data=AdminPanelCallback(action="admin").pack())
            )
        if SHOW_START_MENU_ONCE:
            builder.row(InlineKeyboardButton(text=ABOUT_VPN, callback_data="about_vpn"))
        else:
            builder.row(InlineKeyboardButton(text=BACK, callback_data="start"))

        await edit_or_send_message(
            target_message=target_message,
            text=profile_message,
            reply_markup=builder.as_markup(),
            media_path=image_path,
            disable_web_page_preview=False,
            force_text=True,
        )
    finally:
        await conn.close()


@router.callback_query(F.data == "balance")
async def balance_handler(callback_query: CallbackQuery, session: Any):
    result = await session.fetchrow(
        "SELECT balance FROM connections WHERE tg_id = $1",
        callback_query.from_user.id,
    )
    balance = result["balance"] if result else 0.0
    balance = int(balance)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=PAYMENT, callback_data="pay"))
    builder.row(InlineKeyboardButton(text=BALANCE_HISTORY, callback_data="balance_history"))
    builder.row(InlineKeyboardButton(text=COUPON, callback_data="activate_coupon"))
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    text = BALANCE_MANAGEMENT_TEXT.format(balance=balance)
    image_path = os.path.join("img", "pic.jpg")

    await edit_or_send_message(
        target_message=callback_query.message,
        text=text,
        reply_markup=builder.as_markup(),
        media_path=image_path,
        disable_web_page_preview=False,
    )


@router.callback_query(F.data == "balance_history")
async def balance_history_handler(callback_query: CallbackQuery, session: Any):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=PAYMENT, callback_data="pay"))
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    records = await get_last_payments(callback_query.from_user.id, session)

    if records:
        history_text = BALANCE_HISTORY_HEADER
        for record in records:
            amount = record["amount"]
            payment_system = record["payment_system"]
            status = record["status"]
            date = record["created_at"].strftime("%Y-%m-%d %H:%M:%S")
            history_text += (
                f"<b>Сумма:</b> {amount}₽\n"
                f"<b>Способ оплаты:</b> {payment_system}\n"
                f"<b>Статус:</b> {status}\n"
                f"<b>Дата:</b> {date}\n\n"
            )
    else:
        history_text = "❌ У вас пока нет операций с балансом."

    await edit_or_send_message(
        target_message=callback_query.message,
        text=history_text,
        reply_markup=builder.as_markup(),
        media_path=None,
        disable_web_page_preview=False,
    )


@router.callback_query(F.data == "invite")
@router.message(F.text == "/invite")
async def invite_handler(callback_query_or_message: Message | CallbackQuery):
    chat_id = None
    if isinstance(callback_query_or_message, CallbackQuery):
        chat_id = callback_query_or_message.message.chat.id
        target_message = callback_query_or_message.message
    else:
        chat_id = callback_query_or_message.chat.id
        target_message = callback_query_or_message

    referral_link = get_referral_link(chat_id)
    referral_stats = await get_referral_stats(chat_id)
    invite_message = invite_message_send(referral_link, referral_stats)
    image_path = os.path.join("img", "pic_invite.jpg")

    builder = InlineKeyboardBuilder()
    if INLINE_MODE:
        builder.button(text=INVITE, switch_inline_query="invite")
    else:
        invite_text = INVITE_TEXT_NON_INLINE.format(referral_link=referral_link)
        builder.button(text=INVITE, switch_inline_query=invite_text)
    builder.button(text=QR, callback_data=f"show_referral_qr|{chat_id}")
    if TOP_REFERRAL_BUTTON:
        builder.button(text=TOP_FIVE, callback_data="top_referrals")
    builder.button(text=MAIN_MENU, callback_data="profile")
    builder.adjust(1)

    await edit_or_send_message(
        target_message=target_message,
        text=invite_message,
        reply_markup=builder.as_markup(),
        media_path=image_path,
        disable_web_page_preview=False,
    )


@router.inline_query(F.query.in_(["referral", "ref", "invite"]))
async def inline_referral_handler(inline_query: InlineQuery):
    referral_link = f"https://t.me/{USERNAME_BOT}?start=referral_{inline_query.from_user.id}"
    trial_time_formatted = format_days(TRIAL_TIME)
    results: list[InlineQueryResultArticle] = []

    for index, offer in enumerate(REFERRAL_OFFERS):
        description = offer["description"][:64]
        message_text = offer["message"].format(trial_time=TRIAL_TIME, trial_time_formatted=trial_time_formatted)[:4096]

        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=offer["title"], url=referral_link))

        results.append(
            InlineQueryResultArticle(
                id=str(index),
                title=offer["title"],
                description=description,
                input_message_content=InputTextMessageContent(message_text=message_text, parse_mode=ParseMode.HTML),
                reply_markup=builder.as_markup(),
            )
        )

    await inline_query.answer(results=results, cache_time=86400, is_personal=True)


@router.callback_query(F.data.startswith("show_referral_qr|"))
async def show_referral_qr(callback_query: CallbackQuery):
    try:
        chat_id = callback_query.data.split("|")[1]
        referral_link = get_referral_link(chat_id)

        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(referral_link)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)

        qr_path = f"/tmp/qrcode_referral_{chat_id}.png"
        with open(qr_path, "wb") as f:
            f.write(buffer.read())

        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=BACK, callback_data="invite"))
        builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

        await edit_or_send_message(
            target_message=callback_query.message,
            text="📷 <b>Ваш QR-код для реферальной ссылки.</b>",
            reply_markup=builder.as_markup(),
            media_path=qr_path,
        )

        os.remove(qr_path)

    except Exception as e:
        logger.error(f"Ошибка при генерации QR-кода для реферальной ссылки: {e}", exc_info=True)
        await callback_query.message.answer("❌ Произошла ошибка при создании QR-кода.")
        

@router.callback_query(F.data == "top_referrals")
async def top_referrals_handler(callback_query: CallbackQuery):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        user_referral_count = await conn.fetchval(
            "SELECT COUNT(*) FROM referrals WHERE referrer_tg_id = $1",
            callback_query.from_user.id
        ) or 0

        personal_block = "Твоё место в рейтинге:\n"
        if user_referral_count > 0:
            user_position = await conn.fetchval(
                """
                SELECT COUNT(*) + 1 FROM (
                    SELECT COUNT(*) as cnt 
                    FROM referrals 
                    GROUP BY referrer_tg_id 
                    HAVING COUNT(*) > $1
                ) AS better_users
                """,
                user_referral_count
            )
            personal_block += f"{user_position}. {callback_query.from_user.id} - {user_referral_count} чел."
        else:
            personal_block += "Ты еще не приглашал пользователей в проект."

        top_referrals = await conn.fetch(
            """
            SELECT referrer_tg_id, COUNT(*) as referral_count
            FROM referrals
            GROUP BY referrer_tg_id
            ORDER BY referral_count DESC
            LIMIT 5
            """
        )

        is_admin = callback_query.from_user.id in ADMIN_ID
        rows = ""
        for i, row in enumerate(top_referrals, 1):
            tg_id = str(row["referrer_tg_id"])
            count = row["referral_count"]
            display_id = tg_id if is_admin else f"{tg_id[:5]}*****"
            rows += f"{i}. {display_id} - {count} чел.\n"

        text = TOP_REFERRALS_TEXT.format(personal_block=personal_block, rows=rows)

        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=BACK, callback_data="invite"))
        builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

        await edit_or_send_message(
            target_message=callback_query.message,
            text=text,
            reply_markup=builder.as_markup(),
            media_path=None,
            disable_web_page_preview=False,
        )
    finally:
        await conn.close()
