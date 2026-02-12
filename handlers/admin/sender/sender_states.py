from aiogram.fsm.state import State, StatesGroup


class AdminSender(StatesGroup):
    waiting_for_message = State()
    preview = State()
