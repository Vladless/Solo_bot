import csv

from io import StringIO
from typing import Any

from aiogram.types import BufferedInputFile


async def export_users_csv(session: Any) -> BufferedInputFile:
    """
    Экспорт пользователей в CSV с сортировкой от самого старого к новому.
    """
    query = """
        SELECT 
            tg_id, 
            username, 
            first_name, 
            last_name, 
            language_code, 
            is_bot, 
            balance, 
            trial,
            created_at  
        FROM users
        ORDER BY created_at ASC
    """

    users = await session.fetch(query)

    buffer = StringIO()
    buffer.write("tg_id,username,first_name,last_name,language_code,is_bot,balance,trial,created_at\n")

    for user in users:
        buffer.write(
            f"{user['tg_id']},{user['username']},{user['first_name']},{user['last_name']},"
            f"{user['language_code']},{user['is_bot']},{user['balance']},{user['trial']},"
            f"{user['created_at']}\n"
        )

    buffer.seek(0)

    return BufferedInputFile(file=buffer.getvalue().encode("utf-8-sig"), filename="users_export.csv")


async def export_payments_csv(session: Any) -> BufferedInputFile:
    """
    Экспорт платежей в CSV с сортировкой от самого старого к новому.
    """
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
        ORDER BY p.created_at ASC  -- Сортировка по дате от старых к новым
    """
    payments = await session.fetch(query)
    return _export_payments_csv(payments, "payments_export.csv")


async def export_user_payments_csv(tg_id: int, session: Any) -> BufferedInputFile:
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
    buffer = StringIO()
    buffer.write("tg_id,username,first_name,last_name,amount,payment_system,status,created_at\n")

    for payment in payments:
        buffer.write(
            f"{payment['tg_id']},{payment['username']},{payment['first_name']},{payment['last_name']},"
            f"{payment['amount']},{payment['payment_system']},{payment['status']},{payment['created_at']}\n"
        )

    buffer.seek(0)

    return BufferedInputFile(file=buffer.getvalue().encode("utf-8-sig"), filename=filename)


async def export_referrals_csv(referrer_tg_id: int, session: Any) -> BufferedInputFile | None:
    """
    Формирует CSV-файл со списком рефералов и возвращает его как BufferedInputFile.
    Если у пользователя нет рефералов, возвращает None.
    """
    rows = await session.fetch(
        """
        SELECT
            r.referred_tg_id,
            COALESCE(u.first_name, '') AS first_name,
            COALESCE(u.last_name, '') AS last_name,
            COALESCE(u.username, '') AS username
        FROM referrals r
        JOIN users u ON u.tg_id = r.referred_tg_id
        WHERE r.referrer_tg_id = $1
        ORDER BY r.referred_tg_id
        """,
        referrer_tg_id,
    )

    if not rows:
        return None

    output = StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["Приглашённый (tg_id)", "Имя"])

    for row in rows:
        invited_id = row["referred_tg_id"]
        full_name = row["first_name"].strip() or row["username"] or str(invited_id)
        if row["last_name"]:
            full_name = f"{full_name} {row['last_name']}"
        writer.writerow([invited_id, full_name.strip()])

    output.seek(0)
    csv_data = output.getvalue().encode("utf-8")
    filename = f"referrals_{referrer_tg_id}.csv"

    return BufferedInputFile(file=csv_data, filename=filename)


async def export_hot_leads_csv(session: Any) -> BufferedInputFile:
    """
    Экспорт пользователей, которые делали платежи, но сейчас не имеют ключей.
    Возвращает: tg_id, username, first_name, last_name, updated_at
    """
    query = """
        SELECT DISTINCT u.tg_id, u.username, u.first_name, u.last_name, u.updated_at
        FROM users u
        JOIN payments p ON u.tg_id = p.tg_id
        LEFT JOIN keys k ON u.tg_id = k.tg_id
        WHERE p.status = 'success'
        AND k.tg_id IS NULL
        ORDER BY u.updated_at DESC
    """

    users = await session.fetch(query)

    buffer = StringIO()
    buffer.write("tg_id,username,first_name,last_name,updated_at\n")

    for user in users:
        buffer.write(
            f"{user['tg_id']},{user['username'] or ''},"
            f"{user['first_name'] or ''},{user['last_name'] or ''},"
            f"{user['updated_at']}\n"
        )

    buffer.seek(0)
    return BufferedInputFile(file=buffer.getvalue().encode("utf-8-sig"), filename="hot_leads_export.csv")


async def export_keys_csv(session) -> BufferedInputFile:
    """
    Экспорт подписок в CSV.
    """
    keys = await session.fetch("""
        SELECT tg_id, client_id, email, created_at, expiry_time, key, server_id, is_frozen, alias
        FROM keys
        ORDER BY created_at ASC
    """)

    buffer = StringIO()
    buffer.write("tg_id,client_id,email,created_at,expiry_time,key,server_id,is_frozen,alias\n")

    for row in keys:
        buffer.write(
            f"{row['tg_id']},{row['client_id']},{row['email']},"
            f"{row['created_at']},{row['expiry_time']},{row['key']},"
            f"{row['server_id']},{row['is_frozen']},{row['alias'] or ''}\n"
        )

    buffer.seek(0)
    return BufferedInputFile(file=buffer.getvalue().encode("utf-8-sig"), filename="keys_export.csv")
