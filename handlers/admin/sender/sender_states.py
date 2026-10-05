from aiogram.fsm.state import State, StatesGroup


class AdminSender(StatesGroup):
    waiting_for_channel = State()
    waiting_for_message = State()
    preview = State()
    waiting_for_schedule_datetime = State()
    waiting_for_edit_message = State()
    waiting_for_edit_schedule_datetime = State()


class AdminPoll(StatesGroup):
    waiting_for_question = State()
    waiting_for_options = State()
    preview = State()
