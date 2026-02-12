from aiogram.fsm.state import State, StatesGroup


class TariffCreateState(StatesGroup):
    group = State()
    name = State()
    duration = State()
    price = State()
    traffic = State()
    confirm_more = State()
    device_limit = State()
    vless = State()


class TariffEditState(StatesGroup):
    choosing_field = State()
    editing_value = State()


class TariffSubgroupState(StatesGroup):
    selecting_tariffs = State()
    entering_subgroup_title = State()


class SubgroupEditState(StatesGroup):
    entering_new_title = State()
    confirming_deletion = State()
    editing_tariffs = State()
