import logging
from datetime import datetime
from typing import Optional, Dict, Any, List, Union

from aiogram import Bot

logger = logging.getLogger(__name__)


async def send_admin_notification(
    bot: Bot,
    title: str,
    message: str,
    user_data: Optional[Dict[str, Any]] = None,
    admin_ids: Optional[List[Union[int, str]]] = None
) -> None:
    """
    Отправляет уведомление всем администраторам
    """
    try:
        from config import ADMIN_ID as ADMIN_IDS, NOTIFY_NEW_USERS, NOTIFY_PAYMENTS, NOTIFY_ERRORS, ADMIN_CHAT_ID
        
        # Use provided admin_ids or fall back to config
        admin_ids_to_notify = admin_ids if admin_ids is not None else (ADMIN_IDS if isinstance(ADMIN_IDS, list) else [ADMIN_IDS])
        
        logger.info(f"[NOTIFICATION] Preparing to send notification. Title: {title}")
        logger.info(f"[NOTIFICATION] Admin IDs from config: {admin_ids_to_notify}")
        logger.info(f"[NOTIFICATION] Admin chat ID: {ADMIN_CHAT_ID}")
        logger.info(f"[NOTIFICATION] Notification settings - New users: {NOTIFY_NEW_USERS}, Payments: {NOTIFY_PAYMENTS}, Errors: {NOTIFY_ERRORS}")
        logger.info(f"[NOTIFICATION] User data: {user_data}")
        
        if not admin_ids_to_notify or (isinstance(admin_ids_to_notify, list) and not any(admin_ids_to_notify)):
            logger.error("[NOTIFICATION] No admin IDs found in config or empty list provided")
            return
            
        # Проверяем, что бот инициализирован
        if not bot or not hasattr(bot, 'send_message'):
            logger.error("[NOTIFICATION] Bot instance is not properly initialized")
            return
            
        # Если уведомления отключены в конфиге, выходим
        if ("Ошибка" in title and not NOTIFY_ERRORS):
            logger.warning(f"[NOTIFICATION] Notification '{title}' is disabled in config")
            return
            
        # Формируем текст уведомления
        logger.info(f"[NOTIFICATION] Forming notification text")
        text = f"{title}\n\n"
        
        if user_data:
            user_info = []
            for key, value in user_data.items():
                if value is not None:
                    if key == "ID пользователя":
                        # Форматируем ID как код для удобного копирования
                        user_info.append(f"<b>{key}:</b> <code>{str(value)}</code>")
                    else:
                        user_info.append(f"<b>{key}:</b> {str(value)}")
            if user_info:
                text += "\n".join(user_info)
                logger.info(f"[NOTIFICATION] User info: {user_info}")
            
        logger.info(f"[NOTIFICATION] Final notification text: {text[:100]}...")
        
        # Отправляем уведомление всем администраторам
        for admin_id in admin_ids_to_notify:
            try:
                if not admin_id:
                    logger.warning(f"[NOTIFICATION] Skipping empty admin ID in admin_ids_to_notify")
                    continue
                    
                logger.info(f"[NOTIFICATION] Sending to admin {admin_id}: {text[:100]}...")
                await bot.send_message(
                    chat_id=admin_id,
                    text=text,
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )
                logger.info(f"[NOTIFICATION] Successfully sent to admin {admin_id}")
            except Exception as e:
                error_msg = str(e).lower()
                if "chat not found" in error_msg:
                    logger.error(f"[NOTIFICATION] Chat not found for admin ID: {admin_id}")
                else:
                    logger.error(f"[NOTIFICATION] Failed to send to admin {admin_id}: {error_msg}", exc_info=True)
                    
        logger.info(f"[NOTIFICATION] Completed sending notification to all admins")
        
    except Exception as e:
        logger.error(f"[NOTIFICATION] Critical error in send_admin_notification: {str(e)}", exc_info=True)


async def send_notification(
    bot: Bot,
    user_id: int,
    username: str,
    notification_type: str,
    title: str,
    user_data: Dict[str, Any],
    admin_ids: Optional[List[Union[int, str]]] = None
) -> None:
    """
    Универсальная функция для отправки уведомлений администраторам
    
    :param bot: Экземпляр бота
    :param user_id: ID пользователя
    :param username: Имя пользователя
    :param notification_type: Тип уведомления ("new_user", "payment", "subscription", "error")
    :param title: Заголовок уведомления
    :param user_data: Данные пользователя для отображения
    :param admin_ids: Список ID администраторов для отправки
    """
    try:
        from config import NOTIFY_NEW_USERS, NOTIFY_NEW_SUBSCRIPTIONS, NOTIFY_PAYMENTS, NOTIFY_ERRORS
        
        # Проверяем, разрешены ли уведомления для данного типа
        if notification_type == "new_user" and not NOTIFY_NEW_USERS:
            return
        elif notification_type == "payment" and not NOTIFY_PAYMENTS:
            return
        elif notification_type == "subscription" and not NOTIFY_NEW_SUBSCRIPTIONS:
            return
        elif notification_type == "error" and not NOTIFY_ERRORS:
            return
            
        if not bot or not hasattr(bot, 'send_message'):
            logger.error("[NOTIFICATION] Bot instance is not properly initialized")
            return
            
        # Формируем заголовок
        full_title = title
        
        # Добавляем время только для ошибок
        if notification_type == "error":
            current_time = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
            full_title = f"⚠️ {full_title} [{current_time}]"
        
        logger.info(f"[NOTIFICATION] Preparing to send {notification_type} notification for user {user_id}")
        logger.info(f"[NOTIFICATION] Title: {full_title}")
        logger.info(f"[NOTIFICATION] User data: {user_data}")
        logger.info(f"[NOTIFICATION] Admin IDs: {admin_ids}")
        
        await send_admin_notification(
            bot=bot,
            title=full_title,
            message="",
            user_data=user_data,
            admin_ids=admin_ids
        )
        
        logger.info(f"[NOTIFICATION] Successfully sent {notification_type} notification for user {user_id}")
    except Exception as e:
        logger.error(f"[NOTIFICATION] Error in send_notification: {str(e)}", exc_info=True)


async def notify_new_user(
    bot: Bot,
    user_id: int,
    username: str,
    full_name: str,
    referrer_id: Optional[int] = None,
    admin_ids: Optional[List[Union[int, str]]] = None
) -> None:
    """
    Уведомление о новом пользователе
    
    :param bot: Экземпляр бота
    :param user_id: ID пользователя
    :param username: Имя пользователя
    :param full_name: Полное имя пользователя
    :param referrer_id: ID реферера (если есть)
    :param admin_ids: Список ID администраторов для отправки
    """
    try:
        title = "Новый пользователь"
        user_data = {
            "ID пользователя": user_id,
            "Имя": full_name,
            "Username": f"@{username}" if username else "Не указан",
            "Реферер": f"ID: {referrer_id}" if referrer_id else "Нет"
        }
        
        await send_notification(
            bot=bot,
            user_id=user_id,
            username=username,
            notification_type="new_user",
            title=title,
            user_data=user_data,
            admin_ids=admin_ids
        )
    except Exception as e:
        logger.error(f"[NOTIFICATION] Error in notify_new_user: {str(e)}", exc_info=True)


async def notify_payment(
    bot: Bot,
    user_id: int,
    username: str,
    amount: float,
    tariff_name: str,
    payment_method: str,
    payment_id: str,
    admin_ids: Optional[List[Union[int, str]]] = None
) -> None:
    """
    Уведомление об успешной оплате
    
    :param bot: Экземпляр бота
    :param user_id: ID пользователя
    :param username: Имя пользователя
    :param amount: Сумма оплаты
    :param tariff_name: Название тарифа
    :param payment_method: Метод оплаты
    :param payment_id: ID платежа
    :param admin_ids: Список ID администраторов для отправки
    """
    try:
        title = f"💰 Новый платеж"
        user_data = {
            "ID пользователя": user_id,
            "Username": f"@{username}" if username else "Не указан",
            "Сумма": f"{amount} руб.",
            "Тариф": tariff_name,
            "Метод оплаты": payment_method,
            "ID платежа": payment_id
        }
        
        await send_notification(
            bot=bot,
            user_id=user_id,
            username=username,
            notification_type="payment",
            title=title,
            user_data=user_data,
            admin_ids=admin_ids
        )
    except Exception as e:
        logger.error(f"[NOTIFICATION] Error in notify_payment: {str(e)}", exc_info=True)


async def notify_subscription(
    bot: Bot,
    user_id: int,
    username: str,
    subscription_type: str,
    duration_days: int,
    amount: Optional[float] = None,  # Стоимость подписки
    tariff_name: str = None,
    is_trial: bool = False,
    is_renewal: bool = False,
    user_data: Optional[Dict[str, Any]] = None,
    admin_ids: Optional[List[Union[int, str]]] = None
) -> None:
    """
    Уведомление о новой подписке или продлении существующей
    
    :param bot: Экземпляр бота
    :param user_id: ID пользователя
    :param username: Имя пользователя
    :param subscription_type: Тип подписки (например, "Trial", "Standart", "VIP")
    :param duration_days: Длительность подписки в днях
    :param tariff_name: Название тарифа (опционально)
    :param is_trial: Флаг триальной подписки
    :param is_renewal: Флаг продления подписки
    :param admin_ids: Список ID администраторов для отправки (если None, берется из конфига)
    """
    try:
        from config import NOTIFY_NEW_SUBSCRIPTIONS, NOTIFY_NEW_USERS, ADMIN_CHAT_ID
        
        if not NOTIFY_NEW_SUBSCRIPTIONS and not is_trial:
            logger.info(f"[NOTIFICATION] Subscription notifications are disabled in config")
            return
            
        if not bot or not hasattr(bot, 'send_message'):
            logger.error("[NOTIFICATION] Bot instance is not properly initialized for subscription notification")
            return
            
        # Подготовка сообщения
        current_time = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        
        # Экранируем специальные символы
        def escape_markdown(text: str) -> str:
            if not text:
                return ""
            escape_chars = "_*[]()~`>#+-=|{}.!"
            return ''.join(f'\\{char}' if char in escape_chars else char for char in str(text))
        
        # Формируем заголовок
        if is_trial:
            title = f"Триал активирован"
            if tariff_name and tariff_name != subscription_type:
                title += f" ({tariff_name})"
        else:
            if is_renewal:
                title = f"Продлено {subscription_type}"
            else:
                title = f"Новая подписка {subscription_type}"
            if tariff_name and tariff_name != subscription_type:
                title += f" ({tariff_name})"
        
        # Формируем данные пользователя
        user_data = {
            "ID пользователя": user_id,
            "Юзернейм": f"@{username}" if username else "Не указан",
            "Срок действия": f"{duration_days} дней",
            "Стоимость": f"{amount} руб." if amount is not None else "Бесплатно"
        }
        
        await send_notification(
            bot=bot,
            user_id=user_id,
            username=username,
            notification_type="subscription",
            title=title,
            user_data=user_data,
            admin_ids=admin_ids
        )
    except Exception as e:
        logger.error(f"[NOTIFICATION] Error in notify_subscription: {str(e)}", exc_info=True)


async def send_admin_message(
    bot: Bot,
    message: str,
    title: str = "📢 Сообщение администратору",
    admin_ids: Optional[List[Union[int, str]]] = None,
    send_to_group: bool = True
) -> None:
    """
    Отправляет кастомное сообщение администраторам
    
    :param bot: Экземпляр бота
    :param message: Текст сообщения
    :param title: Заголовок сообщения (по умолчанию "📢 Сообщение администратору")
    :param admin_ids: Список ID администраторов (если None, берется из конфига)
    :param send_to_group: Отправлять ли сообщение в группу админов (если указана в конфиге)
    """
    from config import ADMIN_ID, ADMIN_CHAT_ID
    
    try:
        # Use provided admin_ids or fall back to config
        target_admin_ids = admin_ids if admin_ids is not None else (ADMIN_ID if isinstance(ADMIN_ID, list) else [ADMIN_ID])
        
        if not target_admin_ids and not (send_to_group and ADMIN_CHAT_ID):
            logger.warning("No admin IDs or group chat ID specified for admin message")
            return
        
        # Send to individual admins
        if target_admin_ids:
            await send_admin_notification(
                bot=bot,
                title=title,
                message=message,
                admin_ids=target_admin_ids
            )
            
        # Send to admin group if enabled and chat ID is set
        if send_to_group and ADMIN_CHAT_ID:
            try:
                await bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=f"*{title}*\n\n{message}",
                    parse_mode="MarkdownV2",
                    disable_web_page_preview=True
                )
                logger.info(f"[ADMIN MESSAGE] Sent to admin group {ADMIN_CHAT_ID}")
            except Exception as e:
                logger.error(f"[ADMIN MESSAGE] Error sending to admin group: {e}", exc_info=True)
                
    except Exception as e:
        logger.error(f"[ADMIN MESSAGE] Error in send_admin_message: {e}", exc_info=True)


async def notify_error(
    bot: Bot,
    error: Exception,
    context: Optional[Dict[str, Any]] = None,
    admin_ids: Optional[List[Union[int, str]]] = None
) -> None:
    """
    Отправляет уведомление об ошибке администраторам
    
    :param bot: Экземпляр бота
    :param error: Исключение, которое произошло
    :param context: Дополнительный контекст об ошибке
    :param admin_ids: Список ID администраторов для отправки (если None, берется из конфига)
    """
    from config import NOTIFY_ERRORS, ADMIN_ID, NOTIFY_NEW_SUBSCRIPTIONS, ADMIN_CHAT_ID
    
    if not NOTIFY_ERRORS:
        logger.warning("Уведомления об ошибках отключены в настройках")
        return
    
    # Use provided admin_ids or fall back to config
    admin_ids = admin_ids if admin_ids is not None else (ADMIN_ID if isinstance(ADMIN_ID, list) else [ADMIN_ID])
        
    if not admin_ids:
        logger.error("Не указаны ID администраторов в конфиге")
        return
    
    try:
        # Format error information
        error_type = error.__class__.__name__
        error_message = str(error) or "Без сообщения"
        
        # Get traceback
        import traceback
        tb_list = traceback.format_exception(type(error), error, error.__traceback__)
        tb_text = ''.join(tb_list[-3:])  # Get last 3 lines of traceback
        
        # Add context information if available
        context_info = ""
        if context:
            context_info = "\n\n📋 *Контекст:*\n"
            for key, value in (context.items() if hasattr(context, 'items') else []):
                context_info += f"• *{key}:* `{str(value)[:100]}`\n"
        
        # Format the full message with MarkdownV2 escaping
        def escape_markdown(text: str) -> str:
            if not text:
                return ""
            escape_chars = r'_*[]()~`>#+-=|{}.!'
            return ''.join(f'\\{char}' if char in escape_chars else char for char in str(text))
        
        # Format the error message with proper escaping
        error_type_escaped = escape_markdown(error_type)
        error_message_escaped = escape_markdown(error_message)
        tb_text_escaped = escape_markdown(tb_text)
        
        message = (
            f"❌ *Ошибка в боте*\n\n"
            f"*Тип:* `{error_type_escaped}`\n"
            f"*Сообщение:* `{error_message_escaped}`\n\n"
            f"*Трассировка:*\n```\n{tb_text_escaped}\n```"
            f"{context_info}"
        )
        
        # Send notification to all admins
        for admin_id in admin_ids:
            try:
                logger.info(f"Sending error notification to admin {admin_id}")
                await bot.send_message(
                    chat_id=admin_id,
                    text=message,
                    parse_mode="MarkdownV2",
                    disable_web_page_preview=True
                )
                logger.info(f"Error notification sent to admin {admin_id}")
            except Exception as e:
                logger.error(f"Failed to send error notification to admin {admin_id}: {e}")
    except Exception as e:
        logger.critical(f"Critical error while sending error notification: {e}", exc_info=True)
