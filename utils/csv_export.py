import csv

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from aiogram.types import BufferedInputFile, FSInputFile
from sqlalchemy import exists, func, join, not_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.constants import PAYMENT_SYSTEMS_EXCLUDED
from database.access.resolution import TelegramId, UserId, public_tg_id, resolve_user_optional, user_id_from_legacy_ref
from database.models import Key, Payment, Poll, PollVote, Referral, Tariff, User


POLL_CSV_MAX_FILE_BYTES = 45 * 1024 * 1024
POLL_CSV_MEMORY_LIMIT_BYTES = 1024 * 1024
POLL_CSV_BATCH_SIZE = 500


def _poll_csv_cell(value: object) -> str:
    """Экранирует формулы в текстовых полях CSV."""
    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def _poll_csv_bytes(values: list) -> bytes:
    """Кодирует одну строку CSV без потери текста."""
    buffer = StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(values)
    return buffer.getvalue().encode("utf-8")


def _poll_vote_csv_values(voter, options: list) -> list:
    """Подготавливает поля голоса и полные выбранные ответы."""
    selected = [
        option_id
        for option_id in voter.get("option_ids") or []
        if isinstance(option_id, int) and 0 <= option_id < len(options)
    ]
    voted_at = voter.get("voted_at")
    if voted_at is not None:
        voted_at = voted_at.replace(tzinfo=UTC) if voted_at.tzinfo is None else voted_at.astimezone(UTC)
    return [
        voter["tg_id"],
        _poll_csv_cell(voter.get("username")),
        _poll_csv_cell(voter.get("first_name")),
        _poll_csv_cell(voter.get("last_name")),
        ", ".join(str(option_id + 1) for option_id in selected),
        _poll_csv_cell(" | ".join(str(options[option_id]) for option_id in selected)),
        voted_at.strftime("%Y-%m-%d %H:%M:%S") if voted_at is not None else "",
    ]


def _poll_csv_document(path: Path, filename: str) -> BufferedInputFile | FSInputFile:
    """Оставляет большие части на диске до завершения отправки."""
    if path.stat().st_size <= POLL_CSV_MEMORY_LIMIT_BYTES:
        data = path.read_bytes()
        path.unlink()
        return BufferedInputFile(file=data, filename=filename)
    return FSInputFile(path, filename=filename)


async def iter_poll_votes_csv(
    session: AsyncSession,
    poll: Poll,
    *,
    max_bytes: int = POLL_CSV_MAX_FILE_BYTES,
) -> AsyncIterator[BufferedInputFile | FSInputFile]:
    """Выгружает голоса частями с ограниченной памятью."""
    if poll.is_anonymous:
        raise ValueError("Анонимные опросы не раскрывают голоса клиентов")
    header = b"\xef\xbb\xbf" + _poll_csv_bytes([
        "Telegram ID",
        "Username",
        "Имя",
        "Фамилия",
        "Варианты (номера)",
        "Ответы",
        "Время голоса (UTC)",
    ])
    if max_bytes < len(header):
        raise ValueError("Лимит CSV меньше размера заголовка")
    query = (
        select(PollVote.tg_id, PollVote.option_ids, PollVote.voted_at, User.username, User.first_name, User.last_name)
        .join(Poll, Poll.id == PollVote.poll_id)
        .outerjoin(User, User.tg_id == PollVote.tg_id)
        .where(PollVote.poll_id == poll.id, Poll.is_anonymous.is_(False))
        .order_by(PollVote.voted_at, PollVote.tg_id)
        .execution_options(yield_per=POLL_CSV_BATCH_SIZE)
    )
    from middlewares.session import operation_session

    async with operation_session(session, keep_transaction=True) as reader:
        result = await reader.stream(query)
        try:
            with TemporaryDirectory(prefix="solobot-poll-csv-") as directory:
                path = Path(directory) / "votes.csv"
                output = path.open("wb")
                part = 1
                size = len(header)
                output.write(header)
                try:
                    async for voters in result.mappings().partitions(POLL_CSV_BATCH_SIZE):
                        for voter in voters:
                            row = _poll_csv_bytes(_poll_vote_csv_values(voter, poll.options or []))
                            if len(header) + len(row) > max_bytes:
                                raise ValueError("Одна строка превышает лимит CSV")
                            if size + len(row) > max_bytes:
                                output.close()
                                output = None
                                yield _poll_csv_document(path, f"poll_{poll.id}_votes_part_{part:03d}.csv")
                                path.unlink(missing_ok=True)
                                part += 1
                                output = path.open("wb")
                                output.write(header)
                                size = len(header)
                            output.write(row)
                            size += len(row)
                    output.close()
                    output = None
                    filename = f"poll_{poll.id}_votes.csv" if part == 1 else f"poll_{poll.id}_votes_part_{part:03d}.csv"
                    yield _poll_csv_document(path, filename)
                finally:
                    if output is not None:
                        output.close()
        finally:
            await result.close()


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
        User.id,
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
        "user_id",
    ])

    for user in users:
        writer.writerow([public_tg_id(user[0]), *user[1:]])

    buffer.seek(0)
    return BufferedInputFile(file=buffer.getvalue().encode("utf-8-sig"), filename="users_export.csv")


async def export_payments_csv(session: AsyncSession) -> BufferedInputFile:
    j = join(User, Payment, User.id == Payment.user_id)
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
            User.id,
        )
        .select_from(j)
        .where(Payment.payment_system.notin_(PAYMENT_SYSTEMS_EXCLUDED))
        .order_by(Payment.created_at.asc())
    )

    result = await session.execute(query)
    payments = result.all()

    return _export_payments_csv(payments, "payments_export.csv")


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
        "user_id",
    ])

    for payment in payments:
        writer.writerow([public_tg_id(payment[0]), *payment[1:]])

    buffer.seek(0)
    return BufferedInputFile(file=buffer.getvalue().encode("utf-8-sig"), filename=filename)


async def export_referrals_csv(referrer_tg_id: int, session: AsyncSession) -> BufferedInputFile | None:
    ref_owner = await resolve_user_optional(session, referrer_tg_id)
    if ref_owner is None:
        return None
    j = join(Referral, User, Referral.referred_user_id == User.id)
    query = (
        select(
            User.tg_id,
            func.coalesce(User.first_name, ""),
            func.coalesce(User.last_name, ""),
            func.coalesce(User.username, ""),
            User.id,
        )
        .select_from(j)
        .where(Referral.referrer_user_id == ref_owner.id)
        .order_by(Referral.referred_user_id.asc())
    )

    result = await session.execute(query)
    rows = result.all()

    if not rows:
        return None

    output = StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["Приглашённый (tg_id)", "Имя", "user_id"])

    for invited_tg, first_name, last_name, username, invited_uid in rows:
        invited_tg = public_tg_id(invited_tg)
        invited_id = invited_tg if invited_tg is not None else "—"
        full_name = first_name.strip() or username or str(invited_id)
        if last_name:
            full_name = f"{full_name} {last_name}"
        writer.writerow([invited_id, full_name.strip(), invited_uid])

    output.seek(0)
    return BufferedInputFile(
        file=output.getvalue().encode("utf-8"),
        filename=f"referrals_{referrer_tg_id}.csv",
    )


async def export_hot_leads_csv(session: AsyncSession) -> BufferedInputFile:
    now_ts = int(datetime.now(UTC).timestamp() * 1000)

    stmt = (
        select(
            User.tg_id,
            User.id,
            User.username,
            User.first_name,
            User.last_name,
            User.updated_at,
        )
        .where(
            exists(
                select(Payment.user_id)
                .where(Payment.user_id == User.id)
                .where(Payment.status == "success")
                .where(Payment.amount > 0)
                .where(Payment.payment_system.notin_(PAYMENT_SYSTEMS_EXCLUDED))
            ),
            not_(exists(select(Key.user_id).where(Key.user_id == User.id).where(Key.expiry_time > now_ts))),
        )
        .order_by(User.updated_at.desc())
    )

    result = await session.execute(stmt)
    users = result.all()

    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["tg_id", "username", "first_name", "last_name", "updated_at", "user_id"])
    for row in users:
        writer.writerow([public_tg_id(row.tg_id), row.username, row.first_name, row.last_name, row.updated_at, row.id])

    buffer.seek(0)
    return BufferedInputFile(
        file=buffer.getvalue().encode("utf-8-sig"),
        filename="hot_leads_export.csv",
    )


async def export_keys_csv(session: AsyncSession) -> BufferedInputFile:
    jk = join(Key, User, Key.user_id == User.id)
    j = join(jk, Tariff, Key.tariff_id == Tariff.id, isouter=True)
    query = (
        select(
            User.tg_id,
            Key.client_id,
            Key.email,
            Key.created_at,
            Key.expiry_time,
            Key.key,
            Key.server_id,
            Key.is_frozen,
            Key.alias,
            Tariff.name.label("tariff_name"),
            User.id.label("user_id"),
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
        "user_id",
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
            public_tg_id(row.tg_id),
            row.client_id,
            row.email,
            created_at,
            expiry_time,
            row.key,
            row.server_id,
            row.is_frozen,
            row.alias or "",
            tariff,
            row.user_id,
        ])

    buffer.seek(0)
    return BufferedInputFile(file=buffer.getvalue().encode("utf-8-sig"), filename="keys_export.csv")


async def export_user_all_payments_csv(
    user_id: int | None = None, session: AsyncSession = None, *, tg_id: int | None = None
) -> BufferedInputFile:
    """Выгружает все платежи клиента в CSV."""
    ref = UserId(user_id) if user_id is not None else TelegramId(tg_id)
    user_id = await user_id_from_legacy_ref(session, ref)
    owner = await session.scalar(select(User).where(User.id == user_id)) if user_id is not None else None
    if owner is None:
        buffer = StringIO()
        writer = csv.writer(buffer)
        writer.writerow([
            "id",
            "tg_id",
            "payment_id",
            "amount",
            "currency",
            "payment_system",
            "status",
            "original_amount",
            "created_at",
            "user_id",
        ])
        buffer.seek(0)
        return BufferedInputFile(
            file=buffer.getvalue().encode("utf-8-sig"),
            filename=f"user_{user_id}_payments_full.csv",
        )

    query = (
        select(
            Payment.id,
            User.tg_id,
            Payment.payment_id,
            Payment.amount,
            Payment.currency,
            Payment.payment_system,
            Payment.status,
            Payment.original_amount,
            Payment.created_at,
        )
        .join(User, Payment.user_id == User.id)
        .where(Payment.user_id == owner.id)
        .order_by(Payment.created_at.asc())
    )

    result = await session.execute(query)
    rows = result.all()

    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "id",
        "tg_id",
        "payment_id",
        "amount",
        "currency",
        "payment_system",
        "status",
        "original_amount",
        "created_at",
        "user_id",
    ])

    for (
        internal_id,
        user_tg_id,
        external_payment_id,
        amount,
        currency,
        payment_system,
        status,
        original_amount,
        created_at,
    ) in rows:
        writer.writerow([
            internal_id,
            public_tg_id(user_tg_id),
            external_payment_id or "",
            amount,
            currency,
            payment_system,
            status,
            original_amount if original_amount is not None else "",
            created_at,
            owner.id,
        ])

    buffer.seek(0)
    return BufferedInputFile(
        file=buffer.getvalue().encode("utf-8-sig"),
        filename=f"user_{user_id}_payments_full.csv",
    )
