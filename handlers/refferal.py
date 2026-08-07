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
from sqlalchemy.ext.asyncio import AsyncSession

from bot import bot
from core.bootstrap import BUTTONS_CONFIG, MODES_CONFIG
from database import (
    add_referral,
    add_user,
    get_referral_by_referred_id,
    get_referral_stats,
)
from database.access.resolution import resolve_user_optional
from database.tariffs import get_tariffs
from logger import logger
from services.formatting import get_referral_link
from services.payments.currency_rates import format_for_user
from settings.buttons import BACK, INVITE, MAIN_MENU, QR, TOP_FIVE
from settings.config import (
    ADMIN_ID,
    INLINE_MODE,
    REFERRAL_BONUS_PERCENTAGES,
    REFERRAL_QR,
    TOP_REFERRAL_BUTTON,
    USERNAME_BOT,
)
from settings.texts import (
    INVITE_ROW_BONUS,
    INVITE_ROW_LEVEL,
    INVITE_TEXT,
    INVITE_TEXT_NON_INLINE,
    NEW_REFERRAL_NOTIFICATION,
    REFERRAL_OFFERS,
    REFERRAL_SUCCESS_MSG,
    TOP_REFERRALS_EMPTY_TEXT,
    TOP_REFERRALS_ROW,
    TOP_REFERRALS_TEXT,
)

from .utils import edit_or_send_message, format_days, render_text, safe_answer_inline_query


router = Router()


def generate_referral_qr_file(referral_link: str, chat_id: str) -> str:
    """Генерация QR в файл. Вызывать через run_cpu(). Возвращает путь к файлу."""
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(referral_link)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    qr_path = f"/tmp/qrcode_referral_{chat_id}.png"
    with open(qr_path, "wb") as file:
        file.write(buffer.read())
    return qr_path


@router.callback_query(F.data == "invite")
@router.message(F.text == "/invite")
async def invite_handler(callback_query_or_message: Message | CallbackQuery, session: AsyncSession):
    if isinstance(callback_query_or_message, CallbackQuery):
        chat_id = callback_query_or_message.message.chat.id
        target_message = callback_query_or_message.message
        language_code = callback_query_or_message.from_user.language_code
    else:
        chat_id = callback_query_or_message.chat.id
        target_message = callback_query_or_message
        language_code = callback_query_or_message.from_user.language_code

    referral_link = get_referral_link(chat_id)
    referral_stats = await get_referral_stats(session, chat_id)

    bonus_rows = []
    for level, value in REFERRAL_BONUS_PERCENTAGES.items():
        if isinstance(value, float):
            bonus_value = f"{int(value * 100)}%"
        else:
            bonus_value = await format_for_user(session, chat_id, value, language_code)
        bonus_rows.append(INVITE_ROW_BONUS.format(level=level, value=bonus_value))

    level_rows = [
        INVITE_ROW_LEVEL.format(level=level, value=stats["total"])
        for level, stats in referral_stats["referrals_by_level"].items()
    ]

    total_bonus_text = await format_for_user(session, chat_id, referral_stats["total_referral_bonus"], language_code)
    invite_message = render_text(
        INVITE_TEXT,
        link=referral_link,
        bonus_table="\n".join(bonus_rows),
        level_table="\n".join(level_rows),
        total=referral_stats["total_referrals"],
        bonus=total_bonus_text,
    )
    image_path = os.path.join("img", "pic_invite.jpg")

    inline_mode_enabled = bool(MODES_CONFIG.get("INLINE_MODE_ENABLED", INLINE_MODE))

    builder = InlineKeyboardBuilder()
    if inline_mode_enabled:
        builder.button(text=INVITE, switch_inline_query="invite")
    else:
        invite_text = INVITE_TEXT_NON_INLINE.format(referral_link=referral_link)
        builder.button(text=INVITE, switch_inline_query=invite_text)
    if BUTTONS_CONFIG.get("REFERRAL_QR_BUTTON_ENABLE", REFERRAL_QR):
        builder.button(text=QR, callback_data=f"show_referral_qr|{chat_id}")
    if BUTTONS_CONFIG.get("TOP_REFERRAL_BUTTON_ENABLE", TOP_REFERRAL_BUTTON):
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
async def inline_referral_handler(inline_query: InlineQuery, session: AsyncSession):
    referral_link = f"https://telegram.me/{USERNAME_BOT}?start=referral_{inline_query.from_user.id}"

    trial_tariffs = await get_tariffs(session, group_code="trial")
    if not trial_tariffs:
        await safe_answer_inline_query(inline_query, results=[], cache_time=0)
        return

    trial_days = trial_tariffs[0]["duration_days"]
    trial_time_formatted = format_days(trial_days)

    results: list[InlineQueryResultArticle] = []

    for index, offer in enumerate(REFERRAL_OFFERS):
        message_text = offer["message"].format(trial_time_formatted=trial_time_formatted)[:4096]
        title = offer["title"].format(trial_time_formatted=trial_time_formatted)
        description = offer["description"]

        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=title, url=referral_link))

        results.append(
            InlineQueryResultArticle(
                id=str(index),
                title=title,
                description=description,
                input_message_content=InputTextMessageContent(
                    message_text=message_text,
                    parse_mode=ParseMode.HTML,
                ),
                reply_markup=builder.as_markup(),
            )
        )

    await safe_answer_inline_query(inline_query, results=results, cache_time=60, is_personal=True)


@router.callback_query(F.data.startswith("show_referral_qr|"))
async def show_referral_qr(callback_query: CallbackQuery):
    try:
        from core.executor import run_cpu

        chat_id = callback_query.data.split("|")[1]
        referral_link = get_referral_link(chat_id)
        qr_path = await run_cpu(generate_referral_qr_file, referral_link, chat_id)

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

    except Exception as error:
        logger.error(f"Ошибка при генерации QR-кода для реферальной ссылки: {error}", exc_info=True)
        await callback_query.message.answer("❌ Произошла ошибка при создании QR-кода.")


@router.callback_query(F.data == "top_referrals")
async def top_referrals_handler(callback_query: CallbackQuery, session: AsyncSession):
    user_id = callback_query.from_user.id
    from database.referrals import get_referral_position, get_top_referrals, get_user_referral_count

    user_referral_count = await get_user_referral_count(session, user_id)

    user_position = 0
    if user_referral_count > 0:
        user_position = await get_referral_position(session, user_referral_count)

    top_referrals = await get_top_referrals(session, limit=5)

    is_admin = user_id in ADMIN_ID
    top_rows = []
    for index, row in enumerate(top_referrals, 1):
        referrer_id = str(row["referrer_user_id"])
        display_id = referrer_id if is_admin else f"{referrer_id[:5]}*****"
        top_rows.append(TOP_REFERRALS_ROW.format(index=index, user=display_id, value=row["referral_count"]))

    if user_referral_count > 0:
        text = render_text(
            TOP_REFERRALS_TEXT,
            top_table="\n".join(top_rows),
            place=user_position,
            invited=user_referral_count,
        )
    else:
        text = render_text(TOP_REFERRALS_EMPTY_TEXT, top_table="\n".join(top_rows))

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
            await message.answer("❌ Вы не можете быть реферальной ссылкой самого себя.")
            return

        existing_referral = await get_referral_by_referred_id(session, user_id)
        if existing_referral:
            await message.answer("❌ Вы уже использовали реферальную ссылку.")
            return

        if isinstance(user, dict):
            inserted = await add_user(session=session, **user)
        else:
            inserted = await add_user(
                session=session,
                tg_id=user.id,
                username=getattr(user, "username", None),
                first_name=getattr(user, "first_name", None),
                last_name=getattr(user, "last_name", None),
                language_code=getattr(user, "language_code", None),
                is_bot=getattr(user, "is_bot", False),
            )

        if inserted is None:
            await message.answer("❌ Вы уже зарегистрированы и не можете стать рефералом.")
            return

        await add_referral(session, user_id, referrer_tg_id)

        try:
            ref_notifier = await resolve_user_optional(session, referrer_tg_id)
            if ref_notifier is not None and ref_notifier.tg_id is not None:
                await bot.send_message(
                    int(ref_notifier.tg_id),
                    NEW_REFERRAL_NOTIFICATION.format(referred_id=user_id),
                )
        except Exception as error:
            logger.error(f"Не удалось отправить уведомление пригласившему ({referrer_tg_id}): {error}")

        await message.answer(REFERRAL_SUCCESS_MSG.format(referrer_tg_id=referrer_tg_id))

    except Exception as error:
        logger.error(f"Ошибка при обработке реферальной ссылки {referral_code}: {error}")
        await message.answer("❌ Произошла ошибка при обработке реферальной ссылки.")
