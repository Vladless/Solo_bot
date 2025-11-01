import asyncio

from datetime import datetime, timedelta

import pytz

from aiogram import Bot, Router
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from config import (
    NOTIFICATION_TIME,
    NOTIFY_10H_ENABLED,
    NOTIFY_10H_HOURS,
    NOTIFY_24H_ENABLED,
    NOTIFY_24H_HOURS,
    NOTIFY_DELETE_DELAY,
    NOTIFY_DELETE_KEY,
    NOTIFY_HOT_LEADS,
    NOTIFY_INACTIVE_TRAFFIC,
    NOTIFY_RENEW,
    NOTIFY_RENEW_EXPIRED,
    TRIAL_TIME_DISABLE,
)
from database import (
    add_notification,
    check_notification_time,
    check_notifications_bulk,
    check_tariff_exists,
    delete_key,
    delete_notification,
    get_all_keys,
    get_balance,
    get_last_notification_time,
    get_tariff_by_id,
    get_tariffs_for_cluster,
    update_balance,
    update_key_expiry,
    update_key_tariff,
)
from handlers.keys.operations import delete_key_from_cluster, renew_key_in_cluster
from handlers.notifications.notify_kb import (
    build_change_tariff_kb,
    build_notification_expired_kb,
    build_notification_kb,
)
from handlers.texts import (
    KEY_CANNOT_RENEW_CURRENT,
    KEY_DELETED_MSG,
    KEY_EXPIRED_DELAY_MSG,
    KEY_EXPIRED_NO_DELAY_MSG,
    KEY_EXPIRY,
    get_renewal_message,
)
from handlers.utils import format_hours, format_minutes, get_russian_month
from hooks.hooks import run_hooks
from logger import logger

from .hot_leads_notifications import notify_hot_leads
from .notify_utils import prepare_key_expiry_data, send_messages_with_limit, send_notification
from .special_notifications import notify_inactive_trial_users, notify_users_no_traffic


router = Router()
moscow_tz = pytz.timezone("Europe/Moscow")
notification_lock = asyncio.Lock()


async def periodic_notifications(bot: Bot, *, sessionmaker: async_sessionmaker):
    while True:
        if notification_lock.locked():
            logger.warning("Уведомления уже выполняются. Пропуск...")
            await asyncio.sleep(NOTIFICATION_TIME)
            continue

        async with notification_lock:
            try:
                async with sessionmaker() as session:
                    logger.info("Запуск обработки уведомлений")

                    current_time = int(datetime.now(moscow_tz).timestamp() * 1000)

                    try:
                        keys = await get_all_keys(session=session)
                        keys = [k for k in keys if not k.is_frozen]
                    except Exception as e:
                        logger.error(f"Ошибка при получении ключей: {e}")
                        keys = []

                    if not TRIAL_TIME_DISABLE:
                        try:
                            await notify_inactive_trial_users(bot, session)
                        except Exception as e:
                            logger.error(f"Ошибка в notify_inactive_trial_users: {e}")

                    if NOTIFY_24H_ENABLED:
                        try:
                            threshold_24h = int(
                                (datetime.now(moscow_tz) + timedelta(hours=NOTIFY_24H_HOURS)).timestamp() * 1000
                            )
                            await notify_24h_keys(bot, session, current_time, threshold_24h, keys)
                        except Exception as e:
                            logger.error(f"Ошибка в notify_24h_keys: {e}")

                    if NOTIFY_10H_ENABLED:
                        try:
                            threshold_10h = int(
                                (datetime.now(moscow_tz) + timedelta(hours=NOTIFY_10H_HOURS)).timestamp() * 1000
                            )
                            await notify_10h_keys(bot, session, current_time, threshold_10h, keys)
                        except Exception as e:
                            logger.error(f"Ошибка в notify_10h_keys: {e}")

                    try:
                        await handle_expired_keys(bot, session, current_time, keys)
                    except Exception as e:
                        logger.error(f"Ошибка в handle_expired_keys: {e}")

                    if NOTIFY_INACTIVE_TRAFFIC:
                        try:
                            await notify_users_no_traffic(bot, session, current_time, keys)
                        except Exception as e:
                            logger.error(f"Ошибка в notify_users_no_traffic: {e}")
                    try:
                        await run_hooks("periodic_notifications", bot=bot, session=session, keys=keys)
                    except Exception as e:
                        logger.error(f"Ошибка в хуках periodic_notifications: {e}")

                    if NOTIFY_HOT_LEADS:
                        try:
                            await notify_hot_leads(bot, session)
                        except Exception as e:
                            logger.error(f"Ошибка в notify_hot_leads: {e}")

                    logger.info("Уведомления завершены")
            except Exception as e:
                logger.error(f"Ошибка в periodic_notifications: {e}")

        await asyncio.sleep(NOTIFICATION_TIME)


async def notify_24h_keys(
    bot: Bot,
    session: AsyncSession,
    current_time: int,
    threshold_time_24h: int,
    keys: list,
):
    logger.info(f"Начало проверки подписок, истекающих через {NOTIFY_24H_HOURS} часов.")
    expiring_keys = [key for key in keys if key.expiry_time and current_time < key.expiry_time <= threshold_time_24h]
    logger.info(f"Найдено {len(expiring_keys)} подписок, истекающих через {NOTIFY_24H_HOURS} часов.")

    tg_ids = [getattr(key, "tg_id", key["tg_id"]) for key in expiring_keys]
    emails = [key.email or "" for key in expiring_keys]
    allowed = await check_notifications_bulk(session, "key_24h", NOTIFY_24H_HOURS, tg_ids=tg_ids, emails=emails)

    allowed_set = {(u["tg_id"], u["email"]) for u in allowed}
    messages = []

    for key in expiring_keys:
        tg_id = getattr(key, "tg_id", key["tg_id"])
        email = key.email or ""
        if (tg_id, email) not in allowed_set:
            continue

        notification_id = f"{email}_key_24h"

        can_notify = await check_notification_time(session, tg_id, notification_id, hours=NOTIFY_24H_HOURS)
        if not can_notify:
            continue

        expiry_data = await prepare_key_expiry_data(key, session, current_time)

        notification_text = KEY_EXPIRY.format(
            email=email,
            hours_left_formatted=expiry_data["hours_left_formatted"],
            formatted_expiry_date=expiry_data["formatted_expiry_date"],
            tariff_name=expiry_data["tariff_name"],
            tariff_details=expiry_data["tariff_details"],
        )

        if NOTIFY_RENEW:
            try:
                await process_auto_renew_or_notify(
                    bot,
                    session,
                    key,
                    notification_id,
                    1,
                    "notify_24h.jpg",
                    notification_text,
                )
            except Exception as e:
                logger.error(f"Ошибка авто-продления/уведомления для пользователя {tg_id}: {e}")
                continue
        else:
            keyboard = build_notification_kb(email)
            messages.append({
                "tg_id": tg_id,
                "text": notification_text,
                "photo": "notify_24h.jpg",
                "keyboard": keyboard,
                "notification_id": notification_id,
                "email": email,
            })

    if messages:
        results = await send_messages_with_limit(bot, messages, session=session)
        sent_count = 0
        for msg, result in zip(messages, results, strict=False):
            tg_id = msg["tg_id"]

            await add_notification(session, tg_id, msg["notification_id"])
            if result:
                sent_count += 1
                logger.info(f"Отправлено уведомление об истекающей подписке {msg['email']} пользователю {tg_id}.")
            else:
                logger.warning(
                    f"Не удалось отправить уведомление об истекающей подписке {msg['email']} пользователю {tg_id}."
                )
        logger.info(f"Отправлено {sent_count} уведомлений об истечении подписки через {NOTIFY_24H_HOURS} часов.")

    logger.info(f"Обработка всех уведомлений за {NOTIFY_24H_HOURS} часов завершена.")
    await asyncio.sleep(1)


async def notify_10h_keys(
    bot: Bot,
    session: AsyncSession,
    current_time: int,
    threshold_time_10h: int,
    keys: list,
):
    logger.info(f"Начало проверки подписок, истекающих через {NOTIFY_10H_HOURS} часов.")
    expiring_keys = [key for key in keys if key.expiry_time and current_time < key.expiry_time <= threshold_time_10h]
    logger.info(f"Найдено {len(expiring_keys)} подписок, истекающих через {NOTIFY_10H_HOURS} часов.")

    tg_ids = [key.tg_id for key in expiring_keys]
    emails = [key.email or "" for key in expiring_keys]
    allowed = await check_notifications_bulk(session, "key_10h", NOTIFY_10H_HOURS, tg_ids=tg_ids, emails=emails)

    allowed_set = {(u["tg_id"], u["email"]) for u in allowed}
    messages = []

    for key in expiring_keys:
        tg_id = key.tg_id
        email = key.email or ""
        if (tg_id, email) not in allowed_set:
            continue

        notification_id = f"{email}_key_10h"

        can_notify = await check_notification_time(session, tg_id, notification_id, hours=NOTIFY_10H_HOURS)
        if not can_notify:
            continue

        expiry_data = await prepare_key_expiry_data(key, session, current_time)

        notification_text = KEY_EXPIRY.format(
            email=email,
            hours_left_formatted=expiry_data["hours_left_formatted"],
            formatted_expiry_date=expiry_data["formatted_expiry_date"],
            tariff_name=expiry_data["tariff_name"],
            tariff_details=expiry_data["tariff_details"],
        )

        if NOTIFY_RENEW:
            try:
                await process_auto_renew_or_notify(
                    bot,
                    session,
                    key,
                    notification_id,
                    1,
                    "notify_10h.jpg",
                    notification_text,
                )
            except Exception as e:
                logger.error(f"Ошибка авто-продления/уведомления для пользователя {tg_id}: {e}")
                continue
        else:
            keyboard = build_notification_kb(email)
            messages.append({
                "tg_id": tg_id,
                "text": notification_text,
                "photo": "notify_10h.jpg",
                "keyboard": keyboard,
                "notification_id": notification_id,
                "email": email,
            })

    if messages:
        results = await send_messages_with_limit(bot, messages, session=session)
        sent_count = 0
        for msg, result in zip(messages, results, strict=False):
            tg_id = msg["tg_id"]

            await add_notification(session, tg_id, msg["notification_id"])
            if result:
                sent_count += 1
                logger.info(f"Отправлено уведомление об истекающей подписке {msg['email']} пользователю {tg_id}.")
            else:
                logger.warning(
                    f"Не удалось отправить уведомление об истекающей подписке {msg['email']} пользователю {tg_id}."
                )
        logger.info(f"Отправлено {sent_count} уведомлений об истечении подписки через {NOTIFY_10H_HOURS} часов.")

    logger.info(f"Обработка всех уведомлений за {NOTIFY_10H_HOURS} часов завершена.")
    await asyncio.sleep(1)


async def handle_expired_keys(
    bot: Bot,
    session: AsyncSession,
    current_time: int,
    keys: list,
):
    logger.info("Начало обработки истекших ключей.")

    expired_keys = [key for key in keys if key.expiry_time and key.expiry_time < current_time]
    logger.info(f"Найдено {len(expired_keys)} истекших ключей.")

    tg_ids = [key.tg_id for key in expired_keys]
    emails = [key.email or "" for key in expired_keys]
    users = await check_notifications_bulk(session, "key_expired", 0, tg_ids=tg_ids, emails=emails)

    messages = []

    for key in expired_keys:
        tg_id = key.tg_id
        email = key.email or ""
        client_id = key.client_id
        server_id = key.server_id
        notification_id = f"{email}_key_expired"

        last_notification_time = await get_last_notification_time(session, tg_id, notification_id)

        if NOTIFY_RENEW_EXPIRED:
            try:
                balance = await get_balance(session, tg_id)
                tariffs = await get_tariffs_for_cluster(session, server_id)
                tariff = tariffs[0] if tariffs else None

                if tariff and balance >= tariff["price_rub"]:
                    await process_auto_renew_or_notify(
                        bot,
                        session,
                        key,
                        notification_id,
                        1,
                        "notify_expired.jpg",
                        get_renewal_message(
                            tariff_name=tariff.get("name", ""),
                            traffic_limit=tariff.get("traffic_limit") if tariff.get("traffic_limit") is not None else 0,
                            device_limit=tariff.get("device_limit") if tariff.get("device_limit") is not None else 0,
                            subgroup_title=tariff.get("subgroup_title", ""),
                        ),
                    )

            except Exception as e:
                logger.error(f"Ошибка авто-продления для пользователя {tg_id}: {e}")
                continue

        if NOTIFY_DELETE_KEY:
            delete_immediately = NOTIFY_DELETE_DELAY == 0
            delete_after_delay = False

            if last_notification_time is not None:
                delete_after_delay = (current_time - last_notification_time) / (1000 * 60) >= NOTIFY_DELETE_DELAY
                logger.info(
                    f"Прошло минут={(current_time - last_notification_time) / (1000 * 60):.2f} "
                    f"NOTIFY_DELETE_DELAY={NOTIFY_DELETE_DELAY}"
                )

            if delete_immediately or delete_after_delay:
                try:
                    await delete_key_from_cluster(server_id, email, client_id, session)
                    await delete_key(session, client_id)
                    logger.info(f"🗑 Ключ {client_id} для пользователя {tg_id} успешно удалён.")

                    keyboard = build_notification_expired_kb()
                    messages.append({
                        "tg_id": tg_id,
                        "text": KEY_DELETED_MSG.format(email=email),
                        "photo": "notify_expired.jpg",
                        "keyboard": keyboard,
                        "notification_id": notification_id,
                        "email": email,
                    })
                except Exception as e:
                    logger.error(f"Ошибка удаления ключа {client_id} для пользователя {tg_id}: {e}")
                continue

        if last_notification_time is None and any(u["tg_id"] == tg_id and u["email"] == email for u in users):
            keyboard = build_notification_kb(email)

            if NOTIFY_DELETE_DELAY > 0:
                hours = NOTIFY_DELETE_DELAY // 60
                minutes = NOTIFY_DELETE_DELAY % 60
                if hours > 0 and minutes > 0:
                    time_formatted = f"{format_hours(hours)} и {format_minutes(minutes)}"
                elif hours > 0:
                    time_formatted = format_hours(hours)
                else:
                    time_formatted = format_minutes(minutes)

                delay_message = KEY_EXPIRED_DELAY_MSG.format(email=email, time_formatted=time_formatted)
            else:
                delay_message = KEY_EXPIRED_NO_DELAY_MSG.format(email=email)

            messages.append({
                "tg_id": tg_id,
                "text": delay_message,
                "photo": "notify_expired.jpg",
                "keyboard": keyboard,
                "notification_id": notification_id,
                "email": email,
            })

    if messages:
        results = await send_messages_with_limit(bot, messages, session=session)
        sent_count = 0
        for msg, result in zip(messages, results, strict=False):
            await add_notification(session, msg["tg_id"], msg["notification_id"])
            if result:
                sent_count += 1
                logger.info(f"📢 Уведомление об истекшем ключе {msg['email']} отправлено пользователю {msg['tg_id']}.")
            else:
                logger.warning(
                    f"📢 Не удалось отправить уведомление об истекшем ключе {msg['email']} пользователю {msg['tg_id']}."
                )

        logger.info(f"Отправлено {sent_count} уведомлений об истекших ключах.")

    logger.info("Обработка истекших ключей завершена.")
    await asyncio.sleep(1)


async def process_auto_renew_or_notify(
    bot,
    conn,
    key,
    notification_id: str,
    renewal_period_months: int,
    standard_photo: str,
    standard_caption: str,
):
    tg_id = key.tg_id
    email = key.email or ""
    renew_notification_id = f"{email}_renew"

    try:
        can_renew = await check_notification_time(conn, tg_id, renew_notification_id, hours=24)
        if not can_renew:
            logger.debug(
                f"⏳ Подписка {email} уже продлевалась в течение последних 24 часов, повторное продление отменено."
            )
            return

        balance = await get_balance(conn, tg_id)
        server_id = key.server_id
        tariff_id = key.tariff_id

        tariffs = await get_tariffs_for_cluster(conn, server_id)
        if not tariffs:
            logger.warning(f"⛔ Нет доступных тарифов для продления подписки {email} (сервер: {server_id})")
            return

        selected_tariff = None

        if not tariff_id:
            selected_tariff = None
        else:
            if await check_tariff_exists(conn, tariff_id):
                current_tariff = await get_tariff_by_id(conn, tariff_id)

                forbidden_groups = ["discounts", "discounts_max", "gifts", "trial"]

                try:
                    hook_results = await run_hooks("renewal_forbidden_groups", chat_id=tg_id, admin=False, session=conn)
                    for hook_result in hook_results:
                        additional_groups = hook_result.get("additional_groups", [])
                        forbidden_groups.extend(additional_groups)
                except Exception as e:
                    logger.warning(f"[AUTO_RENEW] Ошибка при получении дополнительных групп: {e}")

                if current_tariff["group_code"] in forbidden_groups:
                    selected_tariff = None
                elif balance >= current_tariff["price_rub"]:
                    selected_tariff = current_tariff
            else:
                selected_tariff = None

        if not selected_tariff:
            expiry_data = await prepare_key_expiry_data(key, conn, int(datetime.now(moscow_tz).timestamp() * 1000))

            use_change_tariff_kb = False

            if tariff_id and await check_tariff_exists(conn, tariff_id):
                current_tariff = await get_tariff_by_id(conn, tariff_id)
                if current_tariff:
                    forbidden_groups = ["discounts", "discounts_max", "gifts", "trial"]
                    try:
                        hook_results = await run_hooks(
                            "renewal_forbidden_groups", chat_id=tg_id, admin=False, session=conn
                        )
                        for hook_result in hook_results:
                            additional_groups = hook_result.get("additional_groups", [])
                            forbidden_groups.extend(additional_groups)
                    except Exception as e:
                        logger.warning(f"[AUTO_RENEW] Ошибка при получении дополнительных групп: {e}")

                    if current_tariff["group_code"] in forbidden_groups:
                        use_change_tariff_kb = True

                        message_text = KEY_CANNOT_RENEW_CURRENT.format(
                            email=email,
                            hours_left_formatted=expiry_data["hours_left_formatted"],
                            formatted_expiry_date=expiry_data["formatted_expiry_date"],
                            tariff_name=expiry_data["tariff_name"],
                            tariff_details=expiry_data["tariff_details"],
                        )
            else:
                use_change_tariff_kb = True

                message_text = KEY_CANNOT_RENEW_CURRENT.format(
                    email=email,
                    hours_left_formatted=expiry_data["hours_left_formatted"],
                    formatted_expiry_date=expiry_data["formatted_expiry_date"],
                    tariff_name=expiry_data["tariff_name"],
                    tariff_details=expiry_data["tariff_details"],
                )

            last_notification_time = await get_last_notification_time(conn, tg_id, notification_id)
            if last_notification_time is not None:
                return
            
            if use_change_tariff_kb:
                keyboard = build_change_tariff_kb(email)
            else:
                keyboard = build_notification_kb(email)

            await add_notification(conn, tg_id, notification_id)
            text_to_send = message_text if "message_text" in locals() else standard_caption
            await send_notification(bot, tg_id, standard_photo, text_to_send, keyboard)
            return

        client_id = key.client_id
        current_expiry = key.expiry_time
        duration_days = selected_tariff["duration_days"]
        selected_tariff["name"]
        renewal_cost = selected_tariff["price_rub"]
        traffic_limit = selected_tariff["traffic_limit"]
        device_limit = selected_tariff["device_limit"]
        total_gb = traffic_limit if traffic_limit else 0

        new_expiry_time = (
            current_expiry
            if current_expiry > datetime.utcnow().timestamp() * 1000
            else datetime.utcnow().timestamp() * 1000
        ) + duration_days * 24 * 60 * 60 * 1000

        formatted_expiry_date = datetime.fromtimestamp(new_expiry_time / 1000, tz=moscow_tz).strftime("%d %B %Y, %H:%M")

        formatted_expiry_date = formatted_expiry_date.replace(
            datetime.fromtimestamp(new_expiry_time / 1000, tz=moscow_tz).strftime("%B"),
            get_russian_month(datetime.fromtimestamp(new_expiry_time / 1000, tz=moscow_tz)),
        )

        logger.info(
            f"Продление подписки {email} на {duration_days} дней для пользователя {tg_id}. Баланс: {balance}, списываем: {renewal_cost}"
        )

        key_subgroup = selected_tariff.get("subgroup_title")

        await renew_key_in_cluster(
            cluster_id=server_id,
            email=email,
            client_id=client_id,
            new_expiry_time=int(new_expiry_time),
            total_gb=total_gb,
            hwid_device_limit=device_limit,
            session=conn,
            target_subgroup=key_subgroup,
            old_subgroup=key_subgroup,
        )
        await update_balance(conn, tg_id, -renewal_cost)
        await update_key_expiry(conn, client_id, int(new_expiry_time))
        await update_key_tariff(conn, client_id, selected_tariff["id"])
        await add_notification(conn, tg_id, renew_notification_id)
        await delete_notification(conn, tg_id, notification_id)

        renewed_message = get_renewal_message(
            tariff_name=selected_tariff["name"],
            traffic_limit=selected_tariff.get("traffic_limit")
            if selected_tariff.get("traffic_limit") is not None
            else 0,
            device_limit=selected_tariff.get("device_limit") if selected_tariff.get("device_limit") is not None else 0,
            expiry_date=formatted_expiry_date,
            subgroup_title=selected_tariff.get("subgroup_title", ""),
        )

        keyboard = build_notification_expired_kb()
        result = await send_notification(bot, tg_id, "notify_expired.jpg", renewed_message, keyboard)
        if result:
            logger.info(f"✅ Уведомление о продлении подписки {email} отправлено пользователю {tg_id}.")
        else:
            logger.warning(f"📢 Не удалось отправить уведомление о продлении подписки {email} пользователю {tg_id}.")

    except Exception as e:
        logger.error(f"❌ Ошибка в process_auto_renew_or_notify: {e}")
