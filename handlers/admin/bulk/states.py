from aiogram.fsm.state import State, StatesGroup


class BulkStates(StatesGroup):
    created_days = State()
    expiry_days = State()
    action_days = State()
    action_gb = State()
