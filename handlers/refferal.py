import os

from io import BytesIO
from typing import Any

import asyncpg
import qrcode

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

from bot import bot
from config import (
    ADMIN_ID,
    DATABASE_URL,
    INLINE_MODE,
    TOP_REFERRAL_BUTTON,
    TRIAL_TIME,
    USERNAME_BOT,
)
from database import add_referral, add_user, check_user_exists, get_referral_by_referred_id, get_referral_stats
from handlers.buttons import (
    BACK,
    INVITE,
    MAIN_MENU,
    QR,
    TOP_FIVE,
)
from handlers.texts import (
    INVITE_TEXT_NON_INLINE,
    NEW_REFERRAL_NOTIFICATION,
    REFERRAL_OFFERS,
    REFERRAL_SUCCESS_MSG,
    TOP_REFERRALS_TEXT,
)
from logger import logger

from .texts import get_referral_link, invite_message_send
from .utils import edit_or_send_message, format_days


router = Router()


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
        user_referral_count = (
            await conn.fetchval("SELECT COUNT(*) FROM referrals WHERE referrer_tg_id = $1", callback_query.from_user.id)
            or 0
        )

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
                user_referral_count,
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


async def handle_referral_link(referral_code: str, message: Message, state: FSMContext, session: Any):
    try:
        referrer_tg_id = int(referral_code)
        user_exists_now = await check_user_exists(message.chat.id)

        if referrer_tg_id == message.chat.id:
            await message.answer("❌ Вы не можете быть реферальной ссылкой самого себя.")
            return

        if user_exists_now:
            await message.answer("❌ Вы уже зарегистрированы и не можете использовать реферальную ссылку.")
            return

        existing_referral = await get_referral_by_referred_id(message.chat.id, session)
        if existing_referral:
            await message.answer("❌ Вы уже использовали реферальную ссылку.")
            return

        await add_referral(message.chat.id, referrer_tg_id, session)

        from_user = message.from_user
        await add_user(
            tg_id=from_user.id,
            username=from_user.username,
            first_name=from_user.first_name,
            last_name=from_user.last_name,
            language_code=from_user.language_code,
            is_bot=from_user.is_bot,
            session=session,
        )

        try:
            await bot.send_message(
                referrer_tg_id,
                NEW_REFERRAL_NOTIFICATION.format(referred_id=message.chat.id),
            )
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление пригласившему ({referrer_tg_id}): {e}")

        await message.answer(REFERRAL_SUCCESS_MSG.format(referrer_tg_id=referrer_tg_id))
        return

    except Exception as e:
        logger.error(f"Ошибка при обработке реферальной ссылки {referral_code}: {e}")
        await message.answer("❌ Произошла ошибка при обработке реферальной ссылки.")
        return
