import csv

from datetime import datetime
from io import StringIO

from aiogram.types import BufferedInputFile
from sqlalchemy import exists, func, join, not_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Key, Payment, Referral, Tariff, User


async def export_users_csv(session: AsyncSession) -> BufferedInputFile:
    query = select(
        User.tg_id,
        User.username,
        User.first_name,
        User.last_name,
        User.language_code,
        User.is_bot,
        User.balance,
        User.trial,
        User.created_at,
    ).order_by(User.created_at.asc())

    result = await session.execute(query)
    users = result.all()

    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "tg_id",
        "username",
        "first_name",
        "last_name",
        "language_code",
        "is_bot",
        "balance",
        "trial",
        "created_at",
    ])

    for user in users:
        writer.writerow(user)

    buffer.seek(0)
    return BufferedInputFile(file=buffer.getvalue().encode("utf-8-sig"), filename="users_export.csv")


async def export_payments_csv(session: AsyncSession) -> BufferedInputFile:
    j = join(User, Payment, User.tg_id == Payment.tg_id)
    query = (
        select(
            User.tg_id,
            User.username,
            User.first_name,
            User.last_name,
            Payment.amount,
            Payment.payment_system,
            Payment.status,
            Payment.created_at,
        )
        .select_from(j)
        .order_by(Payment.created_at.asc())
    )

    result = await session.execute(query)
    payments = result.all()

    return _export_payments_csv(payments, "payments_export.csv")


async def export_user_payments_csv(tg_id: int, session: AsyncSession) -> BufferedInputFile:
    j = join(User, Payment, User.tg_id == Payment.tg_id)
    query = (
        select(
            User.tg_id,
            User.username,
            User.first_name,
            User.last_name,
            Payment.amount,
            Payment.payment_system,
            Payment.status,
            Payment.created_at,
        )
        .select_from(j)
        .where(User.tg_id == tg_id)
        .order_by(Payment.created_at.asc())
    )

    result = await session.execute(query)
    payments = result.all()

    return _export_payments_csv(payments, f"payments_export_{tg_id}.csv")


def _export_payments_csv(payments, filename: str) -> BufferedInputFile:
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "tg_id",
        "username",
        "first_name",
        "last_name",
        "amount",
        "payment_system",
        "status",
        "created_at",
    ])

    for payment in payments:
        writer.writerow(payment)

    buffer.seek(0)
    return BufferedInputFile(file=buffer.getvalue().encode("utf-8-sig"), filename=filename)


async def export_referrals_csv(referrer_tg_id: int, session: AsyncSession) -> BufferedInputFile | None:
    j = join(Referral, User, Referral.referred_tg_id == User.tg_id)
    query = (
        select(
            Referral.referred_tg_id,
            func.coalesce(User.first_name, ""),
            func.coalesce(User.last_name, ""),
            func.coalesce(User.username, ""),
        )
        .select_from(j)
        .where(Referral.referrer_tg_id == referrer_tg_id)
        .order_by(Referral.referred_tg_id.asc())
    )

    result = await session.execute(query)
    rows = result.all()

    if not rows:
        return None

    output = StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["Приглашённый (tg_id)", "Имя"])

    for invited_id, first_name, last_name, username in rows:
        full_name = first_name.strip() or username or str(invited_id)
        if last_name:
            full_name = f"{full_name} {last_name}"
        writer.writerow([invited_id, full_name.strip()])

    output.seek(0)
    return BufferedInputFile(
        file=output.getvalue().encode("utf-8"),
        filename=f"referrals_{referrer_tg_id}.csv",
    )


async def export_hot_leads_csv(session: AsyncSession) -> BufferedInputFile:
    now_ts = int(datetime.utcnow().timestamp() * 1000)

    stmt = (
        select(
            User.tg_id,
            User.username,
            User.first_name,
            User.last_name,
            User.updated_at,
        )
        .where(
            exists(
                select(Payment.tg_id)
                .where(Payment.tg_id == User.tg_id)
                .where(Payment.status == "success")
                .where(Payment.amount > 0)
                .where(Payment.payment_system.notin_(["referral", "coupon", "cashback"]))
            ),
            not_(exists(select(Key.tg_id).where(Key.tg_id == User.tg_id).where(Key.expiry_time > now_ts))),
        )
        .order_by(User.updated_at.desc())
    )

    result = await session.execute(stmt)
    users = result.all()

    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["tg_id", "username", "first_name", "last_name", "updated_at"])
    for user in users:
        writer.writerow(user)

    buffer.seek(0)
    return BufferedInputFile(
        file=buffer.getvalue().encode("utf-8-sig"),
        filename="hot_leads_export.csv",
    )


async def export_keys_csv(session: AsyncSession) -> BufferedInputFile:
    j = join(Key, Tariff, Key.tariff_id == Tariff.id, isouter=True)
    query = (
        select(
            Key.tg_id,
            Key.client_id,
            Key.email,
            Key.created_at,
            Key.expiry_time,
            Key.key,
            Key.server_id,
            Key.is_frozen,
            Key.alias,
            Tariff.name.label("tariff_name"),
        )
        .select_from(j)
        .order_by(Key.created_at.asc())
    )

    result = await session.execute(query)
    keys = result.all()

    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "tg_id",
        "client_id",
        "email",
        "created_at",
        "expiry_time",
        "key",
        "server_id",
        "is_frozen",
        "alias",
        "tariff",
    ])

    for row in keys:
        created_at = (
            datetime.utcfromtimestamp(row.created_at / 1000).strftime("%Y-%m-%d %H:%M:%S") if row.created_at else ""
        )
        expiry_time = (
            datetime.utcfromtimestamp(row.expiry_time / 1000).strftime("%Y-%m-%d %H:%M:%S") if row.expiry_time else ""
        )
        tariff = row.tariff_name or "—"

        writer.writerow([
            row.tg_id,
            row.client_id,
            row.email,
            created_at,
            expiry_time,
            row.key,
            row.server_id,
            row.is_frozen,
            row.alias or "",
            tariff,
        ])

    buffer.seek(0)
    return BufferedInputFile(file=buffer.getvalue().encode("utf-8-sig"), filename="keys_export.csv")
