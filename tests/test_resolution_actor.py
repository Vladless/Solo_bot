import unittest

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from database.access.resolution import (
    ActorSurface,
    ResolvedActor,
    chat_id_for_user,
    resolve_actor_from_identity,
    resolve_actor_from_legacy_ref,
    telegram_chat_id,
)


class ResolveActorFromLegacyRefTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_telegram_surface_when_legacy_equals_user_tg_id(self):
        user = SimpleNamespace(id=42, tg_id=777, identity_id="ident-1")
        session = object()

        with patch("database.access.resolution.resolve_user_optional", new=AsyncMock(return_value=user)):
            actor = await resolve_actor_from_legacy_ref(session, 777)

        self.assertEqual(actor.surface, ActorSurface.TELEGRAM)
        self.assertEqual(actor.billing_user_id, 42)
        self.assertEqual(actor.telegram_chat_id, 777)
        self.assertEqual(actor.identity_id, "ident-1")

    async def test_returns_web_surface_when_user_has_no_tg(self):
        user = SimpleNamespace(id=11, tg_id=None, identity_id="ident-web")
        session = object()

        with patch("database.access.resolution.resolve_user_optional", new=AsyncMock(return_value=user)):
            actor = await resolve_actor_from_legacy_ref(session, 11)

        self.assertEqual(actor.surface, ActorSurface.WEB)
        self.assertEqual(actor.billing_user_id, 11)
        self.assertIsNone(actor.telegram_chat_id)
        self.assertEqual(actor.identity_id, "ident-web")

    async def test_returns_web_surface_for_linked_user_when_legacy_is_internal_id(self):
        user = SimpleNamespace(id=55, tg_id=700700, identity_id="ident-linked")
        session = object()

        with patch("database.access.resolution.resolve_user_optional", new=AsyncMock(return_value=user)):
            actor = await resolve_actor_from_legacy_ref(session, 55)

        self.assertEqual(actor.surface, ActorSurface.WEB)
        self.assertEqual(actor.billing_user_id, 55)
        self.assertEqual(actor.telegram_chat_id, 700700)
        self.assertEqual(actor.identity_id, "ident-linked")

    async def test_returns_unknown_surface_with_fallback_chat_id_when_user_missing(self):
        session = object()

        with patch("database.access.resolution.resolve_user_optional", new=AsyncMock(return_value=None)):
            actor = await resolve_actor_from_legacy_ref(session, 999999)

        self.assertEqual(actor.surface, ActorSurface.UNKNOWN)
        self.assertIsNone(actor.billing_user_id)
        self.assertEqual(actor.telegram_chat_id, 999999)
        self.assertIsNone(actor.identity_id)


class ResolveActorFromIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolves_billing_and_telegram_ids(self):
        identity = SimpleNamespace(id="ident-main")
        user = SimpleNamespace(id=123, tg_id=555777, identity_id="ident-main")
        session = object()

        with patch("database.identities.ensure_billing_user_for_identity", new=AsyncMock(return_value=123)):
            with patch("database.access.resolution.resolve_user_optional", new=AsyncMock(return_value=user)):
                actor = await resolve_actor_from_identity(session, identity)

        self.assertEqual(
            actor,
            ResolvedActor(
                surface=ActorSurface.WEB,
                billing_user_id=123,
                telegram_chat_id=555777,
                identity_id="ident-main",
            ),
        )


class ChatAddressTests(unittest.IsolatedAsyncioTestCase):
    """Синтетический tg_id не является адресом чата."""

    def test_обычный_tg_это_адрес_чата(self):
        self.assertEqual(telegram_chat_id(SimpleNamespace(tg_id=8119047207)), 8119047207)

    def test_синтетический_tg_адресом_не_является(self):
        self.assertIsNone(telegram_chat_id(SimpleNamespace(tg_id=-96)))

    def test_без_tg_адреса_нет(self):
        self.assertIsNone(telegram_chat_id(SimpleNamespace(tg_id=None)))
        self.assertIsNone(telegram_chat_id(None))

    async def test_резолвер_по_user_id_отсекает_синтетический(self):
        for stored, expected in ((8119047207, 8119047207), (-96, None), (None, None)):
            session = SimpleNamespace(scalar=AsyncMock(return_value=stored))
            self.assertEqual(await chat_id_for_user(session, 96), expected, stored)
