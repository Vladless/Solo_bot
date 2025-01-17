from io import StringIO
from typing import Any

from aiogram.types import BufferedInputFile


async def export_users_csv(session: Any) -> BufferedInputFile:
    # Получение данных о платежах пользователя
    query = """
             SELECT 
                u.tg_id, 
                u.username, 
                u.first_name, 
                u.last_name, 
                u.language_code, 
                u.is_bot, 
                c.balance, 
                c.trial 
            FROM users u
            LEFT JOIN connections c ON u.tg_id = c.tg_id
        """

    users = await session.fetch(query)

    buffer = StringIO()
    buffer.write("tg_id,username,first_name,last_name,language_code,is_bot,balance,trial\n")

    # Запись данных
    for user in users:
        buffer.write(
            f"{user['tg_id']},{user['username']},{user['first_name']},{user['last_name']},"
            f"{user['language_code']},{user['is_bot']},{user['balance']},{user['trial']}\n"
        )

    # Перемещение указателя в начало для чтения
    buffer.seek(0)

    return BufferedInputFile(
        file=buffer.getvalue().encode("utf-8-sig"),
        filename="users_export.csv"
    )


async def export_payments_csv(session: Any) -> BufferedInputFile:
    # Получение данных о всех платежах
    query = """
           SELECT 
               u.tg_id, 
               u.username, 
               u.first_name, 
               u.last_name, 
               p.amount, 
               p.payment_system,
               p.status,
               p.created_at 
           FROM users u
           JOIN payments p ON u.tg_id = p.tg_id
       """
    payments = await session.fetch(query)
    return _export_payments_csv(payments, "payments_export.csv")


async def export_user_payments_csv(tg_id: int, session: Any) -> BufferedInputFile:
    # Получение данных о платежах пользователя
    query = """
            SELECT 
                u.tg_id, 
                u.username, 
                u.first_name, 
                u.last_name, 
                p.amount, 
                p.payment_system,
                p.status,
                p.created_at 
            FROM users u 
            JOIN payments p ON u.tg_id = p.tg_id
            WHERE u.tg_id = $1
        """
    payments = await session.fetch(query, tg_id)
    return _export_payments_csv(payments, f"payments_export_{tg_id}.csv")


def _export_payments_csv(payments: list, filename: str) -> BufferedInputFile:
    # Формирование CSV данных через StringIO
    buffer = StringIO()
    buffer.write("tg_id,username,first_name,last_name,amount,payment_system,status,created_at\n")

    # Запись данных
    for payment in payments:
        buffer.write(
            f"{payment['tg_id']},{payment['username']},{payment['first_name']},{payment['last_name']},"
            f"{payment['amount']},{payment['payment_system']},{payment['status']},{payment['created_at']}\n"
        )

    # Перемещение указателя в начало для чтения
    buffer.seek(0)

    return BufferedInputFile(
        file=buffer.getvalue().encode("utf-8-sig"),
        filename=filename
    )
