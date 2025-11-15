from aiogram.fsm.state import State, StatesGroup


class UserEditorState(StatesGroup):
    waiting_for_user_data = State()
    waiting_for_key_name = State()
    waiting_for_balance = State()
    waiting_for_expiry_time = State()
    waiting_for_message_text = State()
    preview_message = State()
    selecting_cluster = State()
    selecting_duration = State()
    selecting_country = State()


class RenewTariffState(StatesGroup):
    selecting_group = State()
    selecting_tariff = State()


class BanUserStates(StatesGroup):
    waiting_for_reason = State()
    waiting_for_ban_duration = State()
    waiting_for_forever_reason = State()
