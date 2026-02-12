from aiogram import Router
from aiogram.fsm.state import State, StatesGroup


router = Router()


class ServerLimitState(StatesGroup):
    waiting_for_limit = State()


class ServerEditState(StatesGroup):
    choosing_field = State()
    editing_value = State()
