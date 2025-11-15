from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import USE_COUNTRY_SELECTION
from database.models import Key, Server
from logger import logger

from ..panel.keyboard import build_admin_back_kb
from .base import router


@router.callback_query(F.data.startswith("transfer_to_server|"))
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

        await session.commit()

        base_text = f"✅ Ключи успешно перенесены на сервер '{new_server_name}', сервер '{old_server_name}' удален!"
        sync_reminder = '\n\n⚠️ Не забудьте сделать "Синхронизацию".'
        final_text = base_text + (sync_reminder if USE_COUNTRY_SELECTION else "")

        await callback_query.message.edit_text(
            text=final_text,
            reply_markup=build_admin_back_kb("clusters"),
        )
    except Exception as e:
        await session.rollback()
        logger.error(f"Ошибка при переносе ключей на сервер {new_server_name}: {e}")
        await callback_query.message.edit_text(
            text=f"❌ Произошла ошибка при переносе ключей: {e}",
            reply_markup=build_admin_back_kb("clusters"),
        )
    finally:
        await state.clear()


@router.callback_query(F.data.startswith("transfer_to_cluster|"))
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

        await session.commit()

        await callback_query.message.edit_text(
            text=(
                f"✅ Ключи успешно перенесены в кластер '<b>{new_cluster_name}</b>', "
                f"сервер '<b>{old_server_name}</b>' и кластер '<b>{old_cluster_name}</b>' удалены!\n\n"
                f'⚠️ Не забудьте сделать "Синхронизацию".'
            ),
            reply_markup=build_admin_back_kb("clusters"),
        )
    except Exception as e:
        await session.rollback()
        logger.error(f"Ошибка при переносе ключей в кластер {new_cluster_name}: {e}")
        await callback_query.message.edit_text(
            text=f"❌ Произошла ошибка при переносе ключей: {e}",
            reply_markup=build_admin_back_kb("clusters"),
        )
    finally:
        await state.clear()
