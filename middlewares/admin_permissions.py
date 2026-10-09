from contextvars import ContextVar
from weakref import WeakSet

from aiogram import BaseMiddleware
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from filters.admin import get_admin_context
from filters.admin_actions import (
    ADMIN_ACTION_ACCESS,
    LIMIT_CALLBACKS,
    LIMIT_PREFIXES,
    AdminActionAccess,
    callback_action_permissions,
    filter_admin_markup,
    granular_permissions_enabled,
    state_action_permissions,
)
from filters.permissions import GRANULAR_ACTIONS_CONFIGURED, PERM_KEY_DEVICES, PERM_KEY_TRAFFIC
from settings.texts import ADMIN_ACTION_PERMISSION_DENIED


_ADMIN_ACTION_STATE: ContextVar[object | None] = ContextVar("admin_action_state", default=None)
_REGISTERED_SESSIONS: WeakSet = WeakSet()


class AdminKeyboardPermissionsMiddleware(BaseRequestMiddleware):
    async def __call__(self, make_request, bot, method):
        access = ADMIN_ACTION_ACCESS.get()
        markup = getattr(method, "reply_markup", None)
        if access is not None and isinstance(markup, InlineKeyboardMarkup):
            state = _ADMIN_ACTION_STATE.get()
            state_data = await state.get_data() if state is not None else {}
            method = method.model_copy(update={"reply_markup": filter_admin_markup(markup, access, state_data)})
        return await make_request(bot, method)


class AdminActionPermissionsMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: Message | CallbackQuery, data: dict):
        if not granular_permissions_enabled():
            return await handler(event, data)
        sender = getattr(event, "from_user", None)
        if sender is None:
            return None
        is_admin, is_super, permissions = await get_admin_context(sender.id, fresh=True)
        access = AdminActionAccess(is_super, permissions if is_admin else frozenset((GRANULAR_ACTIONS_CONFIGURED,)))
        state = data.get("state")
        state_data = await state.get_data() if state is not None else {}
        current_state = await state.get_state() if state is not None else None
        if isinstance(event, CallbackQuery):
            value = event.data or ""
            required = callback_action_permissions(value, state_data)
            if (
                not required
                and not value.startswith((
                    "admin_user:",
                    "admin_users:",
                    "admin_user_key:",
                    "admin_users_key:",
                    "admin_panel:",
                    "abulk:",
                    "cfg_",
                    "back:",
                ))
                and value != "admin"
            ):
                required = state_action_permissions(current_state, state_data)
        else:
            required = state_action_permissions(current_state, state_data)
        if not is_admin or not access.allows(required):
            if isinstance(event, CallbackQuery):
                await event.answer(ADMIN_ACTION_PERMISSION_DENIED, show_alert=True)
            else:
                await event.answer(ADMIN_ACTION_PERMISSION_DENIED)
            return None
        if state is not None:
            limit_permission = LIMIT_CALLBACKS.get(getattr(event, "data", None))
            for prefix, permission in LIMIT_PREFIXES.items():
                if (getattr(event, "data", None) or "").startswith(prefix):
                    limit_permission = permission
                    break
            if isinstance(event, Message) and current_state == "UserEditorState:config_input_addon":
                limit_permission = PERM_KEY_DEVICES if state_data.get("cfg_param") == "devices" else PERM_KEY_TRAFFIC
            if limit_permission:
                touched = set(state_data.get("admin_config_permissions") or ())
                touched.add(limit_permission)
                await state.update_data(admin_config_permissions=sorted(touched))
        bot = data.get("bot") or event.bot
        if bot.session not in _REGISTERED_SESSIONS:
            bot.session.middleware(AdminKeyboardPermissionsMiddleware())
            _REGISTERED_SESSIONS.add(bot.session)
        access_token = ADMIN_ACTION_ACCESS.set(access)
        state_token = _ADMIN_ACTION_STATE.set(state)
        try:
            return await handler(event, data)
        finally:
            _ADMIN_ACTION_STATE.reset(state_token)
            ADMIN_ACTION_ACCESS.reset(access_token)
