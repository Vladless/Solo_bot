from aiogram import Router
from aiogram.fsm.state import State, StatesGroup

from filters.admin import HasPermission
from filters.permissions import PERM_CLUSTERS


router = Router()
router.callback_query.filter(HasPermission(PERM_CLUSTERS))
router.message.filter(HasPermission(PERM_CLUSTERS))


class ServerLimitState(StatesGroup):
    waiting_for_limit = State()


class ServerEditState(StatesGroup):
    choosing_field = State()
    editing_value = State()
