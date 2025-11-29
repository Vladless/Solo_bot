from database import async_session_maker

from .settings.buttons_config import BUTTONS_CONFIG, load_buttons_config, update_buttons_config
from .settings.management_config import MANAGEMENT_CONFIG, load_management_config, update_management_config
from .settings.modes_config import MODES_CONFIG, load_modes_config, update_modes_config
from .settings.money_config import MONEY_CONFIG, load_money_config, update_money_config
from .settings.notifications_config import NOTIFICATIONS_CONFIG, load_notifications_config, update_notifications_config
from .settings.payments_config import PAYMENTS_CONFIG, load_payments_config, update_payments_config
from .settings.tariffs_config import TARIFFS_CONFIG, load_tariffs_config, update_tariffs_config


async def bootstrap() -> None:
    async with async_session_maker() as session:
        await load_buttons_config(session)
        await load_notifications_config(session)
        await load_modes_config(session)
        await load_payments_config(session)
        await load_money_config(session)
        await load_management_config(session)
        await load_tariffs_config(session)
        await session.commit()
