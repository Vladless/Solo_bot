from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.redis_cache import cache_delete, cache_delete_pattern, cache_key
from database.access.resolution import UserId, user_ref_cache_key, user_ref_cache_keys
from database.cache_purge import defer_purge, defer_purge_patterns
from database.models import Identity, IdentitySession, Key, Payment, User


async def invalidate_identity_cache(
    session: AsyncSession,
    *,
    identity_ids: tuple[str, ...] = (),
    user_ids: tuple[int, ...] = (),
    tg_ids: tuple[int, ...] = (),
) -> None:
    """Откладывает очистку кеша прежних владельцев до коммита."""
    users = (
        await session.execute(
            select(User.id, User.tg_id).where(or_(User.id.in_(user_ids), User.identity_id.in_(identity_ids)))
        )
    ).all()
    uids = {int(uid) for uid in user_ids} | {int(row.id) for row in users}
    refs = uids | {int(tg) for tg in tg_ids} | {int(row.tg_id) for row in users if row.tg_id is not None}
    keys = user_ref_cache_keys(*refs)
    keys.extend(
        user_ref_cache_key(prefix, UserId(uid))
        for uid in uids
        for prefix in ("balance", "profile_data", "user_snapshot", "keys_list", "key_count")
    )
    keys.extend(
        cache_key(prefix, ref)
        for ref in refs
        for prefix in (
            "balance",
            "profile_data",
            "user_snapshot",
            "keys_list",
            "key_count",
            "user_squads",
            "summary_counters",
            "referral_stats",
        )
    )
    patterns = [cache_key("my_payments", uid, "*") for uid in uids]
    patterns.extend(cache_key("audit_history", uid, "*") for uid in uids)
    patterns.extend(cache_key("audit_history", "id", uid, "*") for uid in uids)
    for row in (await session.execute(select(Key.email, Key.client_id).where(Key.user_id.in_(uids)))).all():
        keys.extend((
            cache_key("key_details", row.email),
            cache_key("key_email", row.client_id),
            cache_key("key_actions", row.client_id or row.email),
        ))
        patterns.append(cache_key("sub_response", row.email, "*"))
    payment_ids = (
        (
            await session.execute(
                select(Payment.payment_id).where(Payment.user_id.in_(uids), Payment.payment_id.is_not(None))
            )
        )
        .scalars()
        .all()
    )
    keys.extend(cache_key("payment_pending", pid) for pid in payment_ids)
    tokens = (
        (await session.execute(select(IdentitySession.token_hash).where(IdentitySession.identity_id.in_(identity_ids))))
        .scalars()
        .all()
    )
    tokens.extend(
        (
            await session.execute(
                select(Identity.api_token_hash).where(
                    Identity.id.in_(identity_ids), Identity.api_token_hash.is_not(None)
                )
            )
        )
        .scalars()
        .all()
    )
    keys.extend(cache_key("auth_actor", token) for token in tokens)
    keys.extend(cache_key("notif_unread", iid) for iid in identity_ids)
    telegram_ids = {int(tg) for tg in tg_ids if int(tg) > 0}
    telegram_ids.update(int(row.tg_id) for row in users if row.tg_id is not None and int(row.tg_id) > 0)
    keys.extend(cache_key(prefix, tg) for tg in telegram_ids for prefix in ("user_middleware", "ban_status"))
    if not defer_purge(session, *keys):
        for key in keys:
            await cache_delete(key)
    patterns.extend(cache_key("notif_list", iid, "*") for iid in identity_ids)
    patterns.extend(("referral_stats:*", "referral_top:*", "referral_rank:*"))
    if not defer_purge_patterns(session, *patterns):
        for pattern in patterns:
            await cache_delete_pattern(pattern)
