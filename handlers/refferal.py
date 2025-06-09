import os
from io import BytesIO

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
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import bot
from config import ADMIN_ID, INLINE_MODE, TOP_REFERRAL_BUTTON, TRIAL_CONFIG, USERNAME_BOT
from database import (
    add_referral,
    add_user,
    check_user_exists,
    get_referral_by_referred_id,
    get_referral_stats,
)
from database.models import Referral
from handlers.buttons import BACK, INVITE, MAIN_MENU, QR, TOP_FIVE
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
async def invite_handler(
    callback_query_or_message: Message | CallbackQuery, session: AsyncSession
):
    if isinstance(callback_query_or_message, CallbackQuery):
        chat_id = callback_query_or_message.message.chat.id
        target_message = callback_query_or_message.message
    else:
        chat_id = callback_query_or_message.chat.id
        target_message = callback_query_or_message

    referral_link = get_referral_link(chat_id)
    referral_stats = await get_referral_stats(session, chat_id)
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
    referral_link = (
        f"https://t.me/{USERNAME_BOT}?start=referral_{inline_query.from_user.id}"
    )
    trial_days = TRIAL_CONFIG["duration_days"]
    trial_time_formatted = format_days(trial_days)

    results: list[InlineQueryResultArticle] = []

    for index, offer in enumerate(REFERRAL_OFFERS):
        description = offer["description"][:64]
        message_text = offer["message"].format(
            trial_time=trial_days,
            trial_time_formatted=trial_time_formatted
        )[:4096]

        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=offer["title"], url=referral_link))

        results.append(
            InlineQueryResultArticle(
                id=str(index),
                title=offer["title"],
                description=description,
                input_message_content=InputTextMessageContent(
                    message_text=message_text, parse_mode=ParseMode.HTML
                ),
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
        logger.error(
            f"Ошибка при генерации QR-кода для реферальной ссылки: {e}", exc_info=True
        )
        await callback_query.message.answer("❌ Произошла ошибка при создании QR-кода.")


@router.callback_query(F.data == "top_referrals")
async def top_referrals_handler(callback_query: CallbackQuery, session: AsyncSession):
    user_id = callback_query.from_user.id

    result = await session.execute(
        select(func.count())
        .select_from(Referral)
        .where(Referral.referrer_tg_id == user_id)
    )
    user_referral_count = result.scalar_one() or 0

    personal_block = "Твоё место в рейтинге:\n"
    if user_referral_count > 0:
        subquery = (
            select(func.count().label("cnt"))
            .select_from(Referral)
            .group_by(Referral.referrer_tg_id)
            .having(func.count() > user_referral_count)
            .subquery()
        )
        result = await session.execute(select(func.count()).select_from(subquery))
        user_position = result.scalar_one() + 1
        personal_block += f"{user_position}. {user_id} - {user_referral_count} чел."
    else:
        personal_block += "Ты еще не приглашал пользователей в проект."

    result = await session.execute(
        select(
            Referral.referrer_tg_id,
            func.count(Referral.referred_tg_id).label("referral_count"),
        )
        .group_by(Referral.referrer_tg_id)
        .order_by(desc("referral_count"))
        .limit(5)
    )
    top_referrals = result.all()

    is_admin = user_id in ADMIN_ID
    rows = ""
    for i, row in enumerate(top_referrals, 1):
        tg_id = str(row.referrer_tg_id)
        count = row.referral_count
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


async def handle_referral_link(
    referral_code: str,
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user_data: dict | None = None,
):
    try:
        referrer_tg_id = int(referral_code)
        user = user_data or message.from_user or message.chat
        user_id = user["tg_id"] if isinstance(user, dict) else user.id

        if referrer_tg_id == user_id:
            await message.answer(
                "❌ Вы не можете быть реферальной ссылкой самого себя."
            )
            return

        existing_referral = await get_referral_by_referred_id(session, user_id)
        if existing_referral:
            await message.answer("❌ Вы уже использовали реферальную ссылку.")
            return

        user_exists = await check_user_exists(session, user_id)
        if not user_exists:
            if isinstance(user, dict):
                await add_user(session=session, **user)
            else:
                await add_user(
                    session=session,
                    tg_id=user.id,
                    username=getattr(user, "username", None),
                    first_name=getattr(user, "first_name", None),
                    last_name=getattr(user, "last_name", None),
                    language_code=getattr(user, "language_code", None),
                    is_bot=getattr(user, "is_bot", False),
                )

        await add_referral(session, user_id, referrer_tg_id)

        try:
            await bot.send_message(
                referrer_tg_id,
                NEW_REFERRAL_NOTIFICATION.format(referred_id=user_id),
            )
        except Exception as e:
            logger.error(
                f"Не удалось отправить уведомление пригласившему ({referrer_tg_id}): {e}"
            )

        await message.answer(REFERRAL_SUCCESS_MSG.format(referrer_tg_id=referrer_tg_id))

    except Exception as e:
        logger.error(f"Ошибка при обработке реферальной ссылки {referral_code}: {e}")
        await message.answer("❌ Произошла ошибка при обработке реферальной ссылки.")
