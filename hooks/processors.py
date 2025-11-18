from typing import Any

from logger import logger
from .hooks import run_hooks


async def process_cluster_override(
    tg_id: int,
    state_data: dict,
    session: Any,
    plan: int | None = None,
    **kwargs,
) -> str | None:
    """
    Обрабатывает хук cluster_override.
    
    Возвращает название кластера для принудительного выбора или None.
    """
    try:
        results = await run_hooks(
            "cluster_override",
            tg_id=tg_id,
            state_data=state_data,
            session=session,
            plan=plan,
            **kwargs,
        )
        return results[0] if results and results[0] else None
    except Exception as e:
        logger.warning(f"[CLUSTER_OVERRIDE] Ошибка при обработке хука: {e}")
        return None


async def process_cluster_balancer(
    available_clusters: dict,
    session: Any,
    **kwargs,
) -> dict | None:
    """
    Обрабатывает хук cluster_balancer.
    
    Возвращает отфильтрованный словарь кластеров или None (использовать исходный).
    """
    try:
        results = await run_hooks(
            "cluster_balancer",
            available_clusters=available_clusters,
            session=session,
            **kwargs,
        )
        return results[0] if results and results[0] else None
    except Exception as e:
        logger.warning(f"[CLUSTER_BALANCER] Ошибка при обработке хука: {e}")
        return None


async def process_remnawave_webapp_override(
    remnawave_webapp: bool,
    final_link: str,
    session: Any,
    **kwargs,
) -> bool:
    """
    Обрабатывает хук remnawave_webapp_override.
    
    Возвращает bool - использовать ли webapp для подключения устройства.
    """
    if not remnawave_webapp or not final_link:
        return remnawave_webapp

    try:
        results = await run_hooks(
            "remnawave_webapp_override",
            remnawave_webapp=remnawave_webapp,
            final_link=final_link,
            session=session,
            **kwargs,
        )
        if not results:
            return remnawave_webapp

        for result in results:
            if result is True or result is False:
                return result
            elif isinstance(result, dict) and "override" in result:
                return result["override"]

        return remnawave_webapp
    except Exception as e:
        logger.warning(f"[REMNAWAVE_WEBAPP_OVERRIDE] Ошибка при обработке хука: {e}")
        return remnawave_webapp


async def process_happ_cryptolink_override(
    cluster_id: str | None,
    plan: int | None,
    session: Any,
    email: str | None = None,
    tg_id: int | None = None,
    happ_cryptolink: bool = False,
    **kwargs,
) -> bool:
    """
    Обрабатывает хук happ_cryptolink_override.
    
    Возвращает bool - использовать ли криптоссылку для подписки.
    """
    try:
        results = await run_hooks(
            "happ_cryptolink_override",
            cluster_id=cluster_id,
            plan=plan,
            session=session,
            email=email,
            tg_id=tg_id,
            happ_cryptolink=happ_cryptolink,
            **kwargs,
        )
        if not results:
            return happ_cryptolink

        for result in results:
            if result is True or result is False:
                return result

        return happ_cryptolink
    except Exception as e:
        logger.warning(f"[HAPP_CRYPTOLINK_OVERRIDE] Ошибка при обработке хука: {e}")
        return happ_cryptolink


async def process_extract_cryptolink_from_result(
    result: dict,
    cluster_id: str | None,
    plan: int | None,
    session: Any,
    email: str | None = None,
    tg_id: int | None = None,
    need_vless_key: bool = False,
    **kwargs,
) -> str | None:
    """
    Обрабатывает хук happ_cryptolink_override и извлекает криптоссылку из результата API.
    
    Возвращает криптоссылку если нужно использовать, иначе None.
    """
    if need_vless_key:
        return None

    try:
        from core.bootstrap import MODES_CONFIG
        from config import HAPP_CRYPTOLINK

        base_use_crypto_link = bool(MODES_CONFIG.get("HAPP_CRYPTOLINK_ENABLED", HAPP_CRYPTOLINK))
        use_crypto_link = await process_happ_cryptolink_override(
            cluster_id=cluster_id,
            plan=plan,
            session=session,
            email=email,
            tg_id=tg_id,
            happ_cryptolink=base_use_crypto_link,
            **kwargs,
        )

        if not use_crypto_link:
            return None

        happ = result.get("happ") or {}
        if isinstance(happ, dict):
            crypto_link = happ.get("cryptoLink") or happ.get("link")
            if crypto_link:
                return crypto_link

        return None
    except Exception as e:
        logger.warning(f"[EXTRACT_CRYPTOLINK] Ошибка при извлечении криптоссылки: {e}")
        return None


async def process_get_cryptolink_after_renewal(
    email: str,
    cluster_id: str | None,
    plan: int | None,
    session: Any,
    tg_id: int | None = None,
    remnawave_nodes: list | None = None,
    **kwargs,
) -> str | None:
    """
    Получает свежие данные подписки после продления и извлекает криптоссылку если нужно.
    
    Возвращает криптоссылку если хук требует её использования, иначе None.
    """
    if not remnawave_nodes:
        return None

    try:
        from config import REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD
        from panels.remnawave import RemnawaveAPI
        from database import get_tariff_by_id
        from handlers.keys.operations.utils import is_plan_vless

        remna = RemnawaveAPI(remnawave_nodes[0]["api_url"])
        if not await remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
            return None

        subscription_data = await remna.get_subscription_by_username(email)
        if not subscription_data:
            return None

        need_vless_key = False
        if plan:
            tariff = await get_tariff_by_id(session, plan)
            if tariff:
                need_vless_key = is_plan_vless(tariff)

        return await process_extract_cryptolink_from_result(
            result=subscription_data,
            cluster_id=cluster_id,
            plan=plan,
            session=session,
            email=email,
            tg_id=tg_id,
            need_vless_key=need_vless_key,
            **kwargs,
        )
    except Exception as e:
        logger.warning(f"[GET_CRYPTOLINK_AFTER_RENEWAL] Ошибка получения криптоссылки: {e}")
        return None


async def process_intercept_key_creation_message(
    chat_id: int,
    session: Any,
    target_message: Any,
    **kwargs,
) -> bool:
    """
    Обрабатывает хук intercept_key_creation_message.
    
    Возвращает True если нужно прервать выполнение (перехватить сообщение).
    """
    try:
        results = await run_hooks(
            "intercept_key_creation_message",
            chat_id=chat_id,
            session=session,
            target_message=target_message,
            **kwargs,
        )
        return bool(results and results[0])
    except Exception as e:
        logger.warning(f"[INTERCEPT_KEY_CREATION] Ошибка при обработке хука: {e}")
        return False


async def process_key_creation_complete(
    chat_id: int,
    session: Any,
    email: str,
    key_name: str,
    admin: bool = False,
    **kwargs,
) -> list:
    """
    Обрабатывает хук key_creation_complete.
    
    Возвращает список кнопок для добавления в меню после создания ключа.
    """
    try:
        results = await run_hooks(
            "key_creation_complete",
            chat_id=chat_id,
            admin=admin,
            session=session,
            email=email,
            key_name=key_name,
            **kwargs,
        )
        return results if results else []
    except Exception as e:
        logger.warning(f"[KEY_CREATION_COMPLETE] Ошибка при обработке хука: {e}")
        return []


async def process_process_callback_renew_key(
    callback_query: Any,
    state: Any,
    session: Any,
    **kwargs,
) -> list:
    """
    Обрабатывает хук process_callback_renew_key.
    
    Возвращает список кнопок для добавления в меню продления.
    """
    try:
        results = await run_hooks(
            "process_callback_renew_key",
            callback_query=callback_query,
            state=state,
            session=session,
            **kwargs,
        )
        return results if results else []
    except Exception as e:
        logger.warning(f"[PROCESS_CALLBACK_RENEW_KEY] Ошибка при обработке хука: {e}")
        return []


async def process_renewal_forbidden_groups(
    chat_id: int,
    session: Any,
    admin: bool = False,
    **kwargs,
) -> list[str]:
    """
    Обрабатывает хук renewal_forbidden_groups.
    
    Возвращает список дополнительных запрещенных групп для продления.
    """
    try:
        results = await run_hooks(
            "renewal_forbidden_groups",
            chat_id=chat_id,
            admin=admin,
            session=session,
            **kwargs,
        )
        forbidden_groups = []
        for result in results:
            if isinstance(result, dict):
                additional_groups = result.get("additional_groups", [])
                if isinstance(additional_groups, list):
                    forbidden_groups.extend(additional_groups)
        return forbidden_groups
    except Exception as e:
        logger.warning(f"[RENEWAL_FORBIDDEN_GROUPS] Ошибка при обработке хука: {e}")
        return []


async def process_purchase_tariff_group_override(
    chat_id: int,
    session: Any,
    original_group: str,
    admin: bool = False,
    **kwargs,
) -> dict | None:
    """
    Обрабатывает хук purchase_tariff_group_override.
    
    Возвращает dict с ключами:
    - override_group: str - новая группа тарифов
    - discount_info: dict | None - информация о скидке (опционально)
    
    Или None если переопределение не требуется.
    """
    try:
        results = await run_hooks(
            "purchase_tariff_group_override",
            chat_id=chat_id,
            admin=admin,
            session=session,
            original_group=original_group,
            **kwargs,
        )
        for result in results:
            if isinstance(result, dict) and result.get("override_group"):
                return {
                    "override_group": result["override_group"],
                    "discount_info": result.get("discount_info"),
                }
        return None
    except Exception as e:
        logger.warning(f"[PURCHASE_TARIFF_GROUP_OVERRIDE] Ошибка при обработке хука: {e}")
        return None


async def process_renew_tariffs(
    chat_id: int,
    session: Any,
    admin: bool = False,
    **kwargs,
) -> list:
    """
    Обрабатывает хук renew_tariffs.
    
    Возвращает список кнопок для добавления в меню выбора тарифов для продления.
    """
    try:
        results = await run_hooks(
            "renew_tariffs",
            chat_id=chat_id,
            admin=admin,
            session=session,
            **kwargs,
        )
        return results if results else []
    except Exception as e:
        logger.warning(f"[RENEW_TARIFFS] Ошибка при обработке хука: {e}")
        return []


async def process_renewal_complete(
    chat_id: int,
    session: Any,
    email: str,
    client_id: str,
    admin: bool = False,
    **kwargs,
) -> list:
    """
    Обрабатывает хук renewal_complete.
    
    Возвращает список кнопок для добавления в меню после продления подписки.
    """
    try:
        results = await run_hooks(
            "renewal_complete",
            chat_id=chat_id,
            admin=admin,
            session=session,
            email=email,
            client_id=client_id,
            **kwargs,
        )
        return results if results else []
    except Exception as e:
        logger.warning(f"[RENEWAL_COMPLETE] Ошибка при обработке хука: {e}")
        return []


async def process_view_key_menu(
    key_name: str,
    session: Any,
    **kwargs,
) -> list:
    """
    Обрабатывает хук view_key_menu.
    
    Возвращает список кнопок для добавления в меню просмотра ключа.
    """
    try:
        results = await run_hooks(
            "view_key_menu",
            key_name=key_name,
            session=session,
            **kwargs,
        )
        return results if results else []
    except Exception as e:
        logger.warning(f"[VIEW_KEY_MENU] Ошибка при обработке хука: {e}")
        return []


async def process_admin_key_edit_menu(
    email: str,
    session: Any,
    **kwargs,
) -> list:
    """
    Обрабатывает хук admin_key_edit_menu.
    
    Возвращает список кнопок для добавления в меню редактирования ключа в админке.
    """
    try:
        results = await run_hooks(
            "admin_key_edit_menu",
            email=email,
            session=session,
            **kwargs,
        )
        return results if results else []
    except Exception as e:
        logger.warning(f"[ADMIN_KEY_EDIT_MENU] Ошибка при обработке хука: {e}")
        return []


async def process_after_hwid_reset(
    chat_id: int,
    session: Any,
    key_name: str,
    admin: bool = False,
    **kwargs,
) -> bool:
    """
    Обрабатывает хук after_hwid_reset.
    
    Возвращает True если нужно перенаправить пользователя в профиль после сброса устройств.
    """
    try:
        results = await run_hooks(
            "after_hwid_reset",
            chat_id=chat_id,
            admin=admin,
            session=session,
            key_name=key_name,
            **kwargs,
        )
        if not results:
            return False
        return any("redirect_to_profile" in str(result) for result in results)
    except Exception as e:
        logger.warning(f"[AFTER_HWID_RESET] Ошибка при обработке хука: {e}")
        return False


async def process_tariff_menu(
    group_code: str,
    cluster_name: str,
    tg_id: int,
    session: Any,
    **kwargs,
) -> list:
    """
    Обрабатывает хук tariff_menu.
    
    Возвращает список кнопок для добавления в меню выбора тарифов.
    """
    try:
        results = await run_hooks(
            "tariff_menu",
            group_code=group_code,
            cluster_name=cluster_name,
            tg_id=tg_id,
            session=session,
            **kwargs,
        )
        return results if results else []
    except Exception as e:
        logger.warning(f"[TARIFF_MENU] Ошибка при обработке хука: {e}")
        return []


async def process_check_discount_validity(
    chat_id: int,
    session: Any,
    tariff_group: str,
    admin: bool = False,
    **kwargs,
) -> dict | None:
    """
    Обрабатывает хук check_discount_validity.
    
    Возвращает dict с ключами:
    - valid: bool - валидна ли скидка
    - message: str - сообщение об ошибке (если valid=False)
    
    Или None если скидка валидна.
    """
    try:
        results = await run_hooks(
            "check_discount_validity",
            chat_id=chat_id,
            admin=admin,
            session=session,
            tariff_group=tariff_group,
            **kwargs,
        )
        for result in results:
            if isinstance(result, dict) and not result.get("valid", True):
                return {
                    "valid": False,
                    "message": result.get("message", "❌ Скидка недоступна. Пожалуйста, выберите тариф заново."),
                }
        return None
    except Exception as e:
        logger.warning(f"[CHECK_DISCOUNT_VALIDITY] Ошибка при обработке хука: {e}")
        return None


async def process_connect_device_menu(
    chat_id: int,
    session: Any,
    admin: bool = False,
    **kwargs,
) -> list:
    """
    Обрабатывает хук connect_device_menu.
    
    Возвращает список кнопок для добавления в меню подключения устройства.
    """
    try:
        results = await run_hooks(
            "connect_device_menu",
            chat_id=chat_id,
            admin=admin,
            session=session,
            **kwargs,
        )
        return results if results else []
    except Exception as e:
        logger.warning(f"[CONNECT_DEVICE_MENU] Ошибка при обработке хука: {e}")
        return []

