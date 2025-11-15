from aiogram import Router
from aiogram.fsm.state import State, StatesGroup


router = Router()


class AdminClusterStates(StatesGroup):
    waiting_for_cluster_name = State()
    waiting_for_api_url = State()
    waiting_for_inbound_id = State()
    waiting_for_server_name = State()
    waiting_for_subscription_url = State()
    waiting_for_days_input = State()
    waiting_for_new_cluster_name = State()
    waiting_for_new_server_name = State()
    waiting_for_server_transfer = State()
    waiting_for_cluster_transfer = State()
