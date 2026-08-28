import asyncio
import unittest

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from starlette.requests import Request
from starlette.responses import Response


asyncio._validate_client_code_ran = True

from api.v2.routes.auth.password import register_by_email
from api.v2.routes.auth.session import auth_summary
from api.v2.routes.keys.user.core import user_keys
from api.v2.routes.payment_links import create_link as create_payment_link_route
from api.v2.routes.tariffs import purchase_tariff_with_balance
from api.v2.schemas.identities import RegisterByEmailRequest
from api.v2.schemas.payment_links import PaymentLinkCreateRequest
from api.v2.schemas.web_public import TariffPurchaseRequest
from database.identities import attach_telegram, merge_billing_user_into_telegram


def _make_request() -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [],
        "query_string": b"",
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "scheme": "http",
        "http_version": "1.1",
    }
    return Request(scope)


def _scalars_all_result(rows):
    return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))


def _scalar_one_or_none_result(value):
    return SimpleNamespace(scalar_one_or_none=lambda: value)


def _empty_result():
    """Результат execute для запросов, форма которых тесту не важна."""
    return SimpleNamespace(
        scalar_one_or_none=lambda: None,
        scalar_one=lambda: 0,
        scalar=lambda: None,
        scalars=lambda: SimpleNamespace(all=list, first=lambda: None),
        all=list,
    )


class _NestedTransaction:
    """Заглушка session.begin_nested() — асинхронный контекст без транзакции."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class WebEmailRegistrationFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_register_by_email_creates_identity_and_binds_actor(self):
        request = _make_request()
        response = Response()
        session = object()
        body = RegisterByEmailRequest(email="User@Gmail.Com", password="strongpass")
        identity = SimpleNamespace(id="ident-email", tg_id=None)

        with (
            patch("api.v2.routes.auth.password.idb.get_identity_by_email", new=AsyncMock(return_value=None)),
            patch(
                "api.v2.routes.auth.password.idb.create_identity_with_token",
                new=AsyncMock(return_value=(identity, "issued-token")),
            ) as create_identity_with_token_mock,
            patch("api.v2.routes.auth.password.smtp_configured", return_value=False),
            patch("api.v2.routes.auth.password.bind_identity_actor", new=AsyncMock()) as bind_identity_actor_mock,
            patch("api.v2.routes.auth.password.idb.ensure_billing_user_for_identity", new=AsyncMock(return_value=777)),
        ):
            result = await register_by_email(body, request, response, session=session)

        self.assertEqual(result.identity_id, "ident-email")
        self.assertIn("issued-token", " ".join(response.headers.getlist("set-cookie")))
        create_identity_with_token_mock.assert_awaited_once_with(
            session,
            email="user@gmail.com",
            password="strongpass",
            request=request,
        )
        bind_identity_actor_mock.assert_awaited_once_with(request, session, identity)

    async def test_register_by_email_applies_referral_code_to_new_billing_user(self):
        request = _make_request()
        session = object()
        body = RegisterByEmailRequest(
            email="invite@gmail.com", password="strongpass", referral_code="https://example.com/referral/321"
        )
        identity = SimpleNamespace(id="ident-invite", tg_id=None)
        referrer_user = SimpleNamespace(id=321, tg_id=None)

        with (
            patch("api.v2.routes.auth.password.idb.get_identity_by_email", new=AsyncMock(return_value=None)),
            patch(
                "api.v2.routes.auth.password.idb.create_identity_with_token",
                new=AsyncMock(return_value=(identity, "issued-token")),
            ),
            patch("api.v2.routes.auth.password.smtp_configured", return_value=False),
            patch("api.v2.routes.auth.password.bind_identity_actor", new=AsyncMock()),
            patch(
                "api.v2.routes.auth.password.resolve_user_optional", new=AsyncMock(return_value=referrer_user)
            ) as resolve_user_mock,
            patch(
                "api.v2.routes.auth.password.idb.ensure_billing_user_for_identity", new=AsyncMock(return_value=555)
            ) as ensure_billing_user_mock,
            patch("api.v2.routes.auth.password.get_referral_by_referred_id", new=AsyncMock(return_value=None)),
            patch("api.v2.routes.auth.password.add_referral", new=AsyncMock()) as add_referral_mock,
        ):
            result = await register_by_email(body, request, Response(), session=session)

        self.assertEqual(result.identity_id, "ident-invite")
        resolve_user_mock.assert_awaited_once_with(session, 321)
        ensure_billing_user_mock.assert_awaited_once_with(session, identity)
        add_referral_mock.assert_awaited_once_with(session, 555, 321)


class WebTariffPurchaseFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_purchase_tariff_uses_identity_billing_user_and_creates_key(self):
        session = SimpleNamespace(commit=AsyncMock(), scalar=AsyncMock(return_value=1))
        identity = SimpleNamespace(id="ident-email")
        body = TariffPurchaseRequest(
            tariff_id=7,
            selected_device_limit=5,
            selected_traffic_gb=100,
        )
        tariff = {
            "id": 7,
            "is_active": True,
            "duration_days": 30,
            "price_rub": 990,
            "group_code": "base",
            "cooldown_days": 0,
        }

        with (
            patch(
                "api.v2.routes.tariffs.idb.ensure_billing_user_for_identity",
                new=AsyncMock(return_value=501),
            ) as ensure_billing_user_mock,
            patch("api.v2.routes.tariffs.get_tariff_by_id", new=AsyncMock(return_value=tariff)),
            patch("api.v2.routes.tariffs.calculate_config_price", return_value=990),
            patch("api.v2.routes.tariffs.get_balance", new=AsyncMock(return_value=1500.0)),
            patch("api.v2.routes.tariffs.create_vpn_key_headless", new=AsyncMock()) as create_key_mock,
        ):
            result = await purchase_tariff_with_balance(
                body,
                request=_make_request(),
                preview=False,
                session=session,
                identity=identity,
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.charged_rub, 990)
        ensure_billing_user_mock.assert_awaited_once_with(session, identity)
        create_key_mock.assert_awaited_once()
        kwargs = create_key_mock.await_args.kwargs
        self.assertEqual(kwargs["tg_id"], 501)
        self.assertEqual(kwargs["plan"], 7)
        self.assertEqual(kwargs["selected_device_limit"], 5)
        self.assertEqual(kwargs["selected_traffic_gb"], 100)
        self.assertEqual(kwargs["selected_price_rub"], 990)


class WebTariffPaymentLinkFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_payment_link_stores_tariff_purchase_intent_for_billing_user(self):
        session = object()
        identity = SimpleNamespace(id="ident-email")
        request = _make_request()
        body = PaymentLinkCreateRequest(
            identity_id="ident-email",
            amount=1290,
            currency="RUB",
            provider_id="ROBOKASSA",
            success_url="https://example.com/payment-success",
            failure_url="https://example.com/payment-failure",
            metadata={
                "payment_flow": "tariff_purchase",
                "tariff_id": 9,
                "selected_device_limit": 4,
                "selected_traffic_gb": 200,
            },
        )

        with (
            patch(
                "api.v2.routes.payment_links.idb.ensure_billing_user_for_identity",
                new=AsyncMock(return_value=777),
            ) as ensure_billing_user_mock,
            patch(
                "api.v2.routes.payment_links.create_payment_link",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        success=True, payment_id="pid-1", payment_url="https://pay.test", error=None
                    )
                ),
            ) as create_payment_link_mock,
            patch("api.v2.routes.payment_links.create_temporary_data", new=AsyncMock()) as create_temporary_data_mock,
            patch(
                "api.v2.routes.payment_links.get_tariff_by_id",
                new=AsyncMock(return_value={"id": 9, "is_active": True, "cooldown_days": 0}),
            ),
            patch("api.v2.routes.payment_links.is_tariff_visible_for", new=AsyncMock(return_value=True)),
            patch("api.v2.routes.payment_links.get_tariff_cooldown_remaining", new=AsyncMock(return_value=0)),
        ):
            result = await create_payment_link_route(body, request, session=session, identity=identity)

        self.assertTrue(result.success)
        self.assertEqual(result.payment_id, "pid-1")
        ensure_billing_user_mock.assert_awaited_once_with(session, identity)
        create_payment_link_mock.assert_awaited_once()
        payment_request = create_payment_link_mock.await_args.args[1]
        self.assertEqual(payment_request.legacy_user_ref, 777)
        self.assertEqual(payment_request.metadata["tariff_id"], 9)
        create_temporary_data_mock.assert_awaited_once_with(
            session,
            777,
            "waiting_for_payment",
            {
                "tariff_id": 9,
                "required_amount": 1290,
                "selected_price_rub": 1290,
                "selected_device_limit": 4,
                "selected_traffic_limit_gb": 200,
            },
        )


class WebAccountKeysFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_auth_keys_returns_web_identity_keys_without_telegram(self):
        session = object()
        identity = SimpleNamespace(id="ident-email", tg_id=None)
        request = _make_request()
        key_obj = SimpleNamespace(
            email="web-user@example.com",
            alias="Main key",
            client_id="client-1",
            tariff_id=5,
            server_id="eu-1",
            created_at=1700000000000,
            expiry_time=1800000000000,
            key="https://example.com/sub/1",
            remnawave_link=None,
            is_frozen=False,
        )

        with (
            patch("api.v2.routes.keys.user.core._resolve_billing_user_id", new=AsyncMock(return_value=555)),
            patch("api.v2.routes.keys.user.core.get_keys", new=AsyncMock(return_value=[key_obj])) as get_keys_mock,
        ):
            result = await user_keys(request, session=session, identity=identity)

        get_keys_mock.assert_awaited_once_with(session, 555)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].email, "web-user@example.com")
        self.assertEqual(result[0].client_id, "client-1")
        self.assertEqual(result[0].server_id, "eu-1")
        self.assertFalse(result[0].is_frozen)

    async def test_auth_summary_returns_referral_code_for_web_identity(self):
        session = SimpleNamespace(
            execute=AsyncMock(
                side_effect=[
                    SimpleNamespace(scalar_one=lambda: 2),
                    SimpleNamespace(scalar_one=lambda: 1),
                    SimpleNamespace(scalar_one=lambda: 0),
                ]
            ),
            begin_nested=lambda: _NestedTransaction(),
        )
        identity = SimpleNamespace(
            id="ident-email",
            email="web@example.com",
            tg_id=None,
            created_at=None,
            password_set=True,
        )
        request = _make_request()

        with (
            patch("api.v2.routes.auth.session.get_request_actor", return_value=SimpleNamespace(billing_user_id=555)),
            patch("api.v2.routes.auth.session.get_balance", new=AsyncMock(return_value=125.0)),
            patch("api.v2.routes.auth.session.get_trial", new=AsyncMock(return_value=1)),
            patch("api.v2.routes.auth.session.get_keys", new=AsyncMock(return_value=[])),
            patch(
                "api.v2.routes.auth.session.get_referral_stats",
                new=AsyncMock(return_value={"total_referrals": 4, "active_referrals": 2, "total_referral_bonus": 99.5}),
            ),
        ):
            result = await auth_summary(request, session=session, identity=identity)

        self.assertTrue(result.referral_code.startswith("r1_"))
        self.assertEqual(result.referrals_total, 4)
        self.assertEqual(result.referrals_active, 2)
        self.assertEqual(result.referral_bonus_total, 99.5)


class TelegramLinkFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_attach_telegram_updates_identity_and_links_tg_user(self):
        identity_before = SimpleNamespace(id="ident-email", tg_id=None, is_admin=False)
        identity_after = SimpleNamespace(id="ident-email", tg_id=None, is_admin=False)
        session = SimpleNamespace(
            execute=AsyncMock(
                side_effect=[
                    _scalar_one_or_none_result(None),
                    _empty_result(),
                ]
            ),
            flush=AsyncMock(),
            commit=AsyncMock(),
            refresh=AsyncMock(),
        )

        with (
            patch(
                "database.identities.get_identity_by_id",
                new=AsyncMock(side_effect=[identity_before, identity_after]),
            ),
            patch("database.identities.get_identity_by_tg_id", new=AsyncMock(return_value=None)),
            patch("database.identities.merge_billing_user_into_telegram", new=AsyncMock()) as merge_mock,
        ):
            result = await attach_telegram(session, "ident-email", 7007)

        self.assertIs(result, identity_after)
        self.assertEqual(identity_after.tg_id, 7007)
        merge_mock.assert_awaited_once_with(session, "ident-email", 7007)
        session.flush.assert_awaited()
        session.commit.assert_not_called()
        session.refresh.assert_awaited_once_with(identity_after)
        update_stmt = session.execute.await_args_list[1].args[0].compile()
        self.assertEqual(update_stmt.params["identity_id"], "ident-email")
        self.assertEqual(update_stmt.params["tg_id_1"], 7007)

    async def test_merge_billing_user_into_existing_tg_user_moves_subscription_and_payments(self):
        billing_user = SimpleNamespace(
            id=41,
            tg_id=None,
            username="web-user",
            first_name="Web",
            last_name="User",
            language_code="ru",
            is_bot=False,
            balance=250.0,
            trial=2,
            preferred_currency="RUB",
            source_code="site",
        )
        telegram_user = SimpleNamespace(id=77, tg_id=7007)
        execute_results = [
            _scalars_all_result([billing_user]),
            _scalar_one_or_none_result(0),
        ]

        async def execute_side_effect(*args, **kwargs):
            if execute_results:
                return execute_results.pop(0)
            return _empty_result()

        session = SimpleNamespace(
            execute=AsyncMock(side_effect=execute_side_effect),
            add=lambda obj: None,
            flush=AsyncMock(),
            commit=AsyncMock(),
        )

        with (
            patch("database.access.resolution.resolve_user_optional", new=AsyncMock(return_value=telegram_user)),
            patch("database.users.update_balance", new=AsyncMock()) as update_balance_mock,
            patch("database.identities.refresh_tg_mirrors_for_user", new=AsyncMock()) as refresh_mirrors_mock,
            patch("database.users.invalidate_balance_cache", new=AsyncMock()),
            patch("database.users.invalidate_profile_cache", new=AsyncMock()),
        ):
            await merge_billing_user_into_telegram(session, "ident-email", 7007)

        update_balance_mock.assert_awaited_once_with(session, 77, 250.0)
        refresh_mirrors_mock.assert_awaited_once_with(session, 77)
        session.commit.assert_not_called()

        compiled_statements = [
            call.args[0].compile()
            for call in session.execute.await_args_list
            if call.args and hasattr(call.args[0], "compile")
        ]

        self.assertTrue(
            any(
                "UPDATE keys SET user_id" in str(compiled)
                and compiled.params.get("user_id") == 77
                and compiled.params.get("user_id_1") == 41
                for compiled in compiled_statements
            )
        )
        self.assertTrue(
            any(
                "UPDATE payments SET user_id" in str(compiled)
                and compiled.params.get("user_id") == 77
                and compiled.params.get("user_id_1") == 41
                for compiled in compiled_statements
            )
        )
        self.assertTrue(
            any(
                "DELETE FROM users" in str(compiled) and compiled.params.get("id_1") == 41
                for compiled in compiled_statements
            )
        )
        self.assertTrue(
            any(
                "UPDATE users SET identity_id" in str(compiled)
                and compiled.params.get("identity_id") == "ident-email"
                and compiled.params.get("id_1") == 77
                for compiled in compiled_statements
            )
        )
