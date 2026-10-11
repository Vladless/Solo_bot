from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urlsplit

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.admin_permissions import require_admin_action
from api.depends import get_session, verify_identity_admin
from api.v2.schemas.user_actions import (
    CabinetTab,
    CreateUserSubscriptionRequest,
    UserCabinetRequest,
    UserMessageRequest,
)
from core.settings.web_config import get_site_url, is_web_enabled, is_web_open_in_browser
from core.settings.yookassa_autopay_config import is_yookassa_autopay_enabled_for_session
from database import (
    async_session_maker,
    get_servers,
    yookassa_autopay as autopay_dal,
)
from database.access.resolution import UserId, chat_id_for_user, get_user_by_id
from database.identities import detach_email, detach_telegram
from database.models import Identity, User
from database.tariffs import get_tariff_by_id, get_tariffs_for_cluster
from database.users import update_trial
from database.web_notifications import notify_web
from filters.permissions import PERM_KEY_CREATE, PERM_KEY_VIEW, PERM_USERS, PERM_USER_BALANCE
from logger import logger
from services.errors import ServiceError
from services.keys import create_vpn_key_headless
from services.tariffs import ConfigOptionRejected, calculate_config_price, ensure_allowed_config, filter_config_options
from services.tariffs.pricing import resolve_config_base_limits
from services.user_audit import record_admin_user_event
from settings import texts


router = APIRouter()
ADMIN_IDENTITY = Depends(verify_identity_admin)
DATABASE_SESSION = Depends(get_session)
CABINET_TAB_QUERY = Query("keys")


async def _user(session: AsyncSession, user_ref: int) -> User:
    """Находит клиента строго по users.id."""
    user = await get_user_by_id(session, UserId(user_ref))
    if user is None:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    return user


def _bot():
    """Возвращает рабочий экземпляр Telegram-бота."""
    from bot import bot

    return bot


def _cabinet_url(tab: CabinetTab, *, webapp: bool = False) -> str:
    """Возвращает обычную ссылку на раздел кабинета."""
    site_url = get_site_url()
    parts = urlsplit(site_url)
    if not is_web_enabled() or parts.scheme not in {"http", "https"} or not parts.netloc:
        raise HTTPException(status_code=409, detail="Веб-кабинет недоступен")
    query = {"tab": tab}
    if webapp:
        query["webapp"] = "1"
    return f"{site_url.rstrip('/')}/dashboard?{urlencode(query)}"


def _tariff_options(tariff: dict) -> dict:
    """Отдаёт безопасные параметры тарифа для выдачи подписки."""
    devices, traffic = filter_config_options(tariff)
    return {
        "id": int(tariff["id"]),
        "name": tariff.get("name") or "",
        "duration_days": int(tariff.get("duration_days") or 0),
        "price_rub": int(tariff.get("price_rub") or 0),
        "device_limit": tariff.get("device_limit"),
        "traffic_limit": tariff.get("traffic_limit"),
        "configurable": bool(tariff.get("configurable")),
        "device_options": devices,
        "traffic_options_gb": traffic,
        "group_code": tariff.get("group_code"),
        "subgroup_title": tariff.get("subgroup_title"),
    }


def _selected_limits(tariff: dict, body: CreateUserSubscriptionRequest) -> tuple[int | None, int | None]:
    """Проверяет выбранные лимиты по настройкам тарифа."""
    base_devices, base_traffic = resolve_config_base_limits(tariff)
    requested = (body.selected_device_limit, body.selected_traffic_gb)
    if not tariff.get("configurable"):
        if any(
            value is not None and value != base
            for value, base in zip(requested, (base_devices, base_traffic), strict=False)
        ):
            raise HTTPException(status_code=400, detail="У этого тарифа фиксированные лимиты")
        return None, None
    try:
        selected = ensure_allowed_config(tariff, *requested)
    except ConfigOptionRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    for value, checked, base in zip(requested, selected, (base_devices, base_traffic), strict=False):
        if value is not None and checked is None and value != base:
            raise HTTPException(status_code=400, detail="Выбранный лимит недоступен в тарифе")
    return selected


@router.get("/{user_ref}/creation-options")
async def user_creation_options(
    user_ref: int = Path(..., gt=0, description="users.id клиента"),
    identity=ADMIN_IDENTITY,
    session: AsyncSession = DATABASE_SESSION,
):
    """Показывает доступные кластеры и тарифы для новой подписки."""
    await require_admin_action(session, identity, PERM_KEY_CREATE)
    await _user(session, user_ref)
    servers = await get_servers(session)
    clusters = []
    tariffs = {}
    for cluster_id, cluster_servers in servers.items():
        if not any(server.get("enabled", True) for server in cluster_servers):
            continue
        available = [
            tariff
            for tariff in await get_tariffs_for_cluster(session, cluster_id)
            if tariff.get("is_active", True) and int(tariff.get("duration_days") or 0) > 0
        ]
        if not available:
            continue
        clusters.append({"id": cluster_id, "name": cluster_id, "tariff_ids": [int(t["id"]) for t in available]})
        tariffs.update({int(tariff["id"]): _tariff_options(tariff) for tariff in available})
    return {"clusters": clusters, "tariffs": list(tariffs.values())}


@router.post("/{user_ref}/subscriptions")
async def create_user_subscription(
    body: CreateUserSubscriptionRequest,
    user_ref: int = Path(..., gt=0, description="users.id клиента"),
    identity=ADMIN_IDENTITY,
    session: AsyncSession = DATABASE_SESSION,
):
    """Выдаёт новую подписку без списания баланса, как в Telegram."""
    await require_admin_action(session, identity, PERM_KEY_CREATE)
    user = await _user(session, user_ref)
    servers = await get_servers(session)
    cluster_servers = servers.get(body.cluster_id, [])
    if not any(server.get("enabled", True) for server in cluster_servers):
        raise HTTPException(status_code=400, detail="Кластер недоступен")
    available = await get_tariffs_for_cluster(session, body.cluster_id)
    if body.tariff_id not in {int(item["id"]) for item in available if item.get("is_active", True)}:
        raise HTTPException(status_code=400, detail="Тариф недоступен в выбранном кластере")
    tariff = await get_tariff_by_id(session, body.tariff_id)
    if not tariff or not tariff.get("is_active", True):
        raise HTTPException(status_code=404, detail="Тариф не найден")
    duration = int(tariff.get("duration_days") or 0)
    if duration <= 0 or (body.duration_days is not None and body.duration_days != duration):
        raise HTTPException(status_code=400, detail="Выберите срок из выбранного тарифа")
    devices, traffic = _selected_limits(tariff, body)
    expiry = datetime.now(UTC) + timedelta(days=duration)
    price = int(calculate_config_price(tariff, devices, traffic))
    try:
        created = await create_vpn_key_headless(
            session,
            UserId(user.id),
            expiry,
            plan=body.tariff_id,
            selected_device_limit=devices,
            selected_traffic_gb=traffic,
            selected_price_rub=price,
            skip_balance_charge=True,
            forced_cluster=body.cluster_id,
            is_trial=str(tariff.get("group_code") or "").lower() == "trial",
        )
    except ServiceError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from None
    except Exception as exc:
        logger.error("[Site:Clients] Не удалось создать подписку user_id={}: {}", user.id, exc)
        raise HTTPException(status_code=502, detail="Не удалось создать подписку на серверах") from None
    logger.info("[Site:Clients] Администратор создал подписку {} для user_id={}", created.client_id, user.id)
    await record_admin_user_event(
        session,
        identity=identity,
        user_id=user.id,
        event_type="admin_user_subscription_created",
        path=f"/users/{user.id}/subscriptions",
        metadata={"client_id": created.client_id, "tariff_id": body.tariff_id},
    )
    return {
        "ok": True,
        "client_id": created.client_id,
        "email": created.email,
        "expiry_time": int(expiry.timestamp() * 1000),
        "link": created.final_link,
    }


@router.post("/{user_ref}/trial/restore")
async def restore_user_trial(
    user_ref: int = Path(..., gt=0, description="users.id клиента"),
    identity=ADMIN_IDENTITY,
    session: AsyncSession = DATABASE_SESSION,
):
    """Возвращает право на получение пробного периода."""
    await require_admin_action(session, identity, PERM_KEY_CREATE)
    user = await _user(session, user_ref)
    if user.trial in (0, -1):
        return {"ok": True, "trial": int(user.trial)}
    await update_trial(session, UserId(user.id), 0)
    await record_admin_user_event(
        session,
        identity=identity,
        user_id=user.id,
        event_type="admin_user_trial_restored",
        path=f"/users/{user.id}/trial/restore",
    )
    return {"ok": True, "trial": 0}


@router.post("/{user_ref}/message")
async def send_user_message(
    body: UserMessageRequest,
    user_ref: int = Path(..., gt=0, description="users.id клиента"),
    identity=ADMIN_IDENTITY,
    session: AsyncSession = DATABASE_SESSION,
):
    """Доставляет текст в Telegram и уведомления кабинета клиента."""
    await require_admin_action(session, identity, PERM_USERS)
    user = await _user(session, user_ref)
    delivered = []
    failed = []
    chat_id = await chat_id_for_user(session, UserId(user.id))
    if chat_id is not None:
        try:
            await _bot().send_message(chat_id=chat_id, text=body.text, parse_mode=None)
            delivered.append("telegram")
        except Exception as exc:
            logger.warning("[Site:Clients] Telegram не доставил сообщение user_id={}: {}", user.id, exc)
            failed.append("telegram")
    if user.identity_id:
        try:
            async with async_session_maker() as notify_session:
                notification = await notify_web(
                    notify_session,
                    user_ref=UserId(user.id),
                    type="message",
                    title=body.text.splitlines()[0][:120],
                    message=body.text,
                )
                if notification is not None:
                    await notify_session.commit()
                    delivered.append("cabinet")
                else:
                    failed.append("cabinet")
        except Exception as exc:
            logger.warning("[Site:Clients] Кабинет не принял сообщение user_id={}: {}", user.id, exc)
            failed.append("cabinet")
    if not delivered:
        raise HTTPException(status_code=502 if failed else 409, detail="Сообщение не доставлено клиенту")
    await record_admin_user_event(
        session,
        identity=identity,
        user_id=user.id,
        event_type="admin_user_message_sent",
        path=f"/users/{user.id}/message",
        metadata={"delivered": delivered, "failed": failed},
    )
    return {"ok": True, "delivered": delivered, "failed": failed}


async def _detach_user_identity(session: AsyncSession, user_ref: int, channel: str) -> dict:
    """Отвязывает канал через общую проверку последнего входа."""
    user = await _user(session, user_ref)
    linked = await session.get(Identity, user.identity_id) if user.identity_id else None
    if linked is None:
        raise HTTPException(status_code=409, detail="У клиента нет привязанного веб-аккаунта")
    if getattr(linked, "email" if channel == "email" else "tg_id") is None:
        return {"ok": True}
    detached = await (detach_email if channel == "email" else detach_telegram)(session, linked.id)
    if detached is None:
        raise HTTPException(status_code=409, detail="Нельзя отвязать единственный способ входа")
    return {"ok": True}


@router.delete("/{user_ref}/identity/email")
async def detach_user_email(
    user_ref: int = Path(..., gt=0, description="users.id клиента"),
    identity=ADMIN_IDENTITY,
    session: AsyncSession = DATABASE_SESSION,
):
    """Отвязывает email, сохраняя вход через Telegram."""
    await require_admin_action(session, identity, PERM_USERS)
    result = await _detach_user_identity(session, user_ref, "email")
    await record_admin_user_event(
        session,
        identity=identity,
        user_id=user_ref,
        event_type="admin_user_email_detached",
        path=f"/users/{user_ref}/identity/email",
    )
    return result


@router.delete("/{user_ref}/identity/telegram")
async def detach_user_telegram(
    user_ref: int = Path(..., gt=0, description="users.id клиента"),
    identity=ADMIN_IDENTITY,
    session: AsyncSession = DATABASE_SESSION,
):
    """Отвязывает Telegram, сохраняя вход через email."""
    await require_admin_action(session, identity, PERM_USERS)
    result = await _detach_user_identity(session, user_ref, "telegram")
    await record_admin_user_event(
        session,
        identity=identity,
        user_id=user_ref,
        event_type="admin_user_telegram_detached",
        path=f"/users/{user_ref}/identity/telegram",
    )
    return result


@router.get("/{user_ref}/cabinet")
async def get_user_cabinet(
    user_ref: int = Path(..., gt=0, description="users.id клиента"),
    tab: CabinetTab = CABINET_TAB_QUERY,
    identity=ADMIN_IDENTITY,
    session: AsyncSession = DATABASE_SESSION,
):
    """Показывает ссылку кабинета без авторизации от имени клиента."""
    await require_admin_action(session, identity, PERM_USERS)
    user = await _user(session, user_ref)
    return {"url": _cabinet_url(tab), "can_send": await chat_id_for_user(session, UserId(user.id)) is not None}


@router.post("/{user_ref}/cabinet")
async def send_user_cabinet(
    body: UserCabinetRequest,
    user_ref: int = Path(..., gt=0, description="users.id клиента"),
    identity=ADMIN_IDENTITY,
    session: AsyncSession = DATABASE_SESSION,
):
    """Отправляет клиенту кнопку нужного раздела кабинета."""
    await require_admin_action(session, identity, PERM_USERS)
    user = await _user(session, user_ref)
    url = _cabinet_url(body.tab, webapp=not is_web_open_in_browser())
    chat_id = await chat_id_for_user(session, UserId(user.id))
    if chat_id is None:
        raise HTTPException(status_code=409, detail="У клиента нет Telegram")
    button_text = getattr(texts, "ADMIN_CLIENT_CABINET_BUTTON", "Личный кабинет")
    button = (
        InlineKeyboardButton(text=button_text, url=url)
        if is_web_open_in_browser()
        else InlineKeyboardButton(text=button_text, web_app=WebAppInfo(url=url))
    )
    try:
        await _bot().send_message(
            chat_id=chat_id,
            text=getattr(texts, "ADMIN_CLIENT_CABINET_MESSAGE", "Откройте раздел в личном кабинете 👇"),
            parse_mode=None,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[button]]),
        )
    except Exception as exc:
        logger.warning("[Site:Clients] Не удалось отправить кабинет user_id={}: {}", user.id, exc)
        raise HTTPException(status_code=502, detail="Не удалось отправить ссылку клиенту") from None
    await record_admin_user_event(
        session,
        identity=identity,
        user_id=user.id,
        event_type="admin_user_cabinet_sent",
        path=f"/users/{user.id}/cabinet",
        metadata={"tab": body.tab},
    )
    return {"ok": True, "delivered": ["telegram"], "failed": []}


@router.get("/{user_ref}/autorenew")
async def get_user_autorenew(
    user_ref: int = Path(..., gt=0, description="users.id клиента"),
    identity=ADMIN_IDENTITY,
    session: AsyncSession = DATABASE_SESSION,
):
    """Показывает автопродления и ссылку для личного согласия клиента."""
    await require_admin_action(session, identity, PERM_KEY_VIEW)
    user = await _user(session, user_ref)
    cards = await autopay_dal.list_cards(session, user.id)
    subscriptions = await autopay_dal.list_subscriptions(session, user.id)
    try:
        setup_url = _cabinet_url("keys")
    except HTTPException:
        setup_url = None
    return {
        "enabled": await is_yookassa_autopay_enabled_for_session(session),
        "customer_setup_url": setup_url,
        "requires_customer_consent": True,
        "cards": [
            {
                "id": card.id,
                "type": card.card_type,
                "mask": card.card_mask,
                "bank_name": card.bank_name,
                "active": bool(card.is_active),
            }
            for card in cards
        ],
        "subscriptions": [
            {
                "id": sub.id,
                "client_id": sub.client_id,
                "amount": sub.amount,
                "gross_amount": float(sub.accepted_gross_amount)
                if sub.accepted_gross_amount is not None
                else sub.amount,
                "period_days": sub.period_days,
                "active": bool(sub.is_active),
                "processing": bool(sub.is_processing),
                "next_payment_date": sub.next_payment_date.isoformat() if sub.next_payment_date else None,
            }
            for sub in subscriptions
        ],
    }


@router.delete("/{user_ref}/autorenew/{subscription_id}")
async def disable_user_autorenew(
    user_ref: int = Path(..., gt=0, description="users.id клиента"),
    subscription_id: int = Path(..., gt=0),
    identity=ADMIN_IDENTITY,
    session: AsyncSession = DATABASE_SESSION,
):
    """Отменяет автопродление только у выбранного клиента."""
    await require_admin_action(session, identity, PERM_USER_BALANCE)
    user = await _user(session, user_ref)
    if not await autopay_dal.cancel_subscription(session, user.id, subscription_id):
        raise HTTPException(status_code=404, detail="Автопродление не найдено у клиента")
    await record_admin_user_event(
        session,
        identity=identity,
        user_id=user.id,
        event_type="admin_user_autorenew_disabled",
        path=f"/users/{user.id}/autorenew/{subscription_id}",
        metadata={"subscription_id": subscription_id},
    )
    return {"ok": True}
