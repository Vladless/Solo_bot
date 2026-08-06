from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import MODES_CONFIG
from database.models import Key, Server
from filters.admin import IsAdminFilter
from logger import logger
from settings.config import USE_COUNTRY_SELECTION

from ..panel.headers import menu_text, quote
from ..panel.keyboard import build_admin_back_kb
from .base import router


@router.callback_query(F.data.startswith("transfer_to_server|"), IsAdminFilter())
async def handle_server_transfer(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    try:
        data = callback_query.data.split("|")
        new_server_name = data[1]
        old_server_name = data[2]

        user_data = await state.get_data()
        cluster_name = user_data.get("cluster_name")

        await session.execute(update(Key).where(Key.server_id == old_server_name).values(server_id=new_server_name))

        await session.execute(
            delete(Server).where(
                Server.cluster_name == cluster_name,
                Server.server_name == old_server_name,
            )
        )

        use_country_selection = bool(MODES_CONFIG.get("COUNTRY_SELECTION_ENABLED", USE_COUNTRY_SELECTION))

        final_text = menu_text(
            "Перенос завершён",
            f"Ключи переехали на <b>{new_server_name}</b>.",
            quote(f"Сервер <b>{old_server_name}</b> удалён."),
            quote("⚠️ Не забудьте выполнить синхронизацию.") if use_country_selection else "",
        )

        await callback_query.message.edit_text(
            text=final_text,
            reply_markup=build_admin_back_kb("clusters"),
        )
    except Exception as e:
        await session.rollback()
        logger.error(f"Ошибка при переносе ключей на сервер {new_server_name}: {e}")
        await callback_query.message.edit_text(
            text=menu_text(
                "Перенос ключей",
                f"❌ Не удалось перенести подписки: {e}",
                markup=build_admin_back_kb("clusters"),
            ),
            reply_markup=build_admin_back_kb("clusters"),
        )
    finally:
        await state.clear()


@router.callback_query(F.data.startswith("transfer_to_cluster|"), IsAdminFilter())
async def handle_cluster_transfer(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    try:
        data = callback_query.data.split("|")
        new_cluster_name = data[1]
        old_cluster_name = data[2]
        old_server_name = data[3]

        user_data = await state.get_data()
        cluster_name = user_data.get("cluster_name")

        await session.execute(update(Key).where(Key.server_id == old_server_name).values(server_id=new_cluster_name))
        await session.execute(update(Key).where(Key.server_id == old_cluster_name).values(server_id=new_cluster_name))

        await session.execute(
            delete(Server).where(
                Server.cluster_name == cluster_name,
                Server.server_name == old_server_name,
            )
        )

        await callback_query.message.edit_text(
            text=(
                menu_text(
                    "Перенос ключей",
                    f"✅ Подписки переехали в кластер <b>{new_cluster_name}</b>.",
                    quote(f"Сервер <b>{old_server_name}</b> и кластер <b>{old_cluster_name}</b> удалены."),
                    quote("⚠️ Не забудьте синхронизировать."),
                    markup=build_admin_back_kb("clusters"),
                )
            ),
            reply_markup=build_admin_back_kb("clusters"),
        )
    except Exception as e:
        await session.rollback()
        logger.error(f"Ошибка при переносе ключей в кластер {new_cluster_name}: {e}")
        await callback_query.message.edit_text(
            text=menu_text(
                "Перенос ключей",
                f"❌ Не удалось перенести подписки: {e}",
                markup=build_admin_back_kb("clusters"),
            ),
            reply_markup=build_admin_back_kb("clusters"),
        )
    finally:
        await state.clear()
