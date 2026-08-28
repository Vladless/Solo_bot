import ast
import re
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LOOPS = (ROOT / "core" / "tasks" / "loop_tasks.py").read_text(encoding="utf-8")
MANAGER = (ROOT / "core" / "tasks" / "periodic_manager.py").read_text(encoding="utf-8")
API_MAIN = (ROOT / "api" / "main.py").read_text(encoding="utf-8")
API_MGMT = (ROOT / "api" / "v1" / "routes" / "management.py").read_text(encoding="utf-8")
DB_COUPONS = (ROOT / "database" / "coupons.py").read_text(encoding="utf-8")
BOT_COUPONS = (ROOT / "handlers" / "coupons.py").read_text(encoding="utf-8")
SITE_COUPONS = (ROOT / "services" / "coupons.py").read_text(encoding="utf-8")
BULK = (ROOT / "handlers" / "admin" / "bulk" / "operations.py").read_text(encoding="utf-8")


class BackupLoopTests(unittest.TestCase):
    """Цикл бэкапов не должен умирать навсегда от одного исключения."""

    def test_вызов_обёрнут_в_try(self):
        body = LOOPS[LOOPS.index("async def backup_loop") :]
        body = body[: body.index("\n\n\nasync def")]
        tree = ast.parse(ast.unparse(ast.parse(body)))
        loops = [n for n in ast.walk(tree) if isinstance(n, ast.While)]
        self.assertTrue(loops)
        self.assertTrue(any(isinstance(x, ast.Try) for x in ast.walk(loops[0])))

    def test_отмена_пробрасывается_а_не_глотается(self):
        body = LOOPS[LOOPS.index("async def backup_loop") :]
        self.assertIn("except asyncio.CancelledError:\n            raise", body)

    def test_сон_остаётся_в_цикле_после_ошибки(self):
        body = LOOPS[LOOPS.index("async def backup_loop") :]
        body = body[: body.index("\n\n\nasync def")]
        self.assertLess(body.index("except Exception"), body.index("await asyncio.sleep(BACKUP_TIME)"))


class DeadLoopVisibilityTests(unittest.TestCase):
    def test_упавший_цикл_попадает_в_лог(self):
        self.assertIn("def _report_loop_exit", MANAGER)
        self.assertIn("task.add_done_callback(partial(self._report_loop_exit, loop_task.task_id))", MANAGER)

    def test_отменённый_цикл_не_считается_падением(self):
        body = MANAGER[MANAGER.index("def _report_loop_exit") :][:700]
        self.assertLess(body.index("task.cancelled()"), body.index("task.exception()"))


class BackgroundTaskTests(unittest.TestCase):
    """Задачи без сильной ссылки может собрать GC на полпути."""

    def test_аудит_api_держит_ссылку(self):
        self.assertNotIn("asyncio.create_task(\n        record_api_access_event_background", API_MAIN)
        self.assertNotIn("asyncio.create_task(\n            record_api_access_event_background", API_MAIN)
        self.assertEqual(API_MAIN.count("spawn(\n        record_api_access_event_background"), 1)
        self.assertEqual(API_MAIN.count("spawn(\n            record_api_access_event_background"), 1)
        self.assertIn("from core.executor import spawn", API_MAIN)

    def test_бэкап_по_api_держит_ссылку_и_не_падает_молча(self):
        body = API_MGMT[API_MGMT.index("async def trigger_backup") :][:800]
        self.assertIn("spawn(_run_backup())", body)
        self.assertNotIn("asyncio.create_task(_run_backup())", body)
        self.assertIn("except Exception as error:", body)


class CouponSlotTests(unittest.TestCase):
    """Лимит купона держится условием в самом UPDATE, а не проверкой заранее."""

    def test_занятие_слота_атомарно(self):
        body = DB_COUPONS[DB_COUPONS.index("async def claim_coupon_slot") :]
        body = body[: body.index("async def release_coupon_slot")]
        self.assertIn("used < Coupon.usage_limit", body)
        self.assertIn(".where(", body, "лимит держится условием самого UPDATE")
        self.assertIn("return claimed", body)

    def test_безлимитный_купон_не_отсекается(self):
        """usage_limit nullable: сравнение с NULL сделало бы такой купон неактивируемым."""
        body = DB_COUPONS[DB_COUPONS.index("async def claim_coupon_slot") :]
        body = body[: body.index("async def release_coupon_slot")]
        self.assertIn("Coupon.usage_limit.is_(None)", body)
        self.assertIn("func.coalesce(Coupon.usage_count, 0)", body)

    def test_слот_можно_вернуть(self):
        body = DB_COUPONS[DB_COUPONS.index("async def release_coupon_slot") :]
        body = body[: body.index("async def update_coupon_usage_count")]
        self.assertIn("usage_count=used - 1", body)
        self.assertIn("used > 0", body)

    def test_старое_имя_осталось_рабочим(self):
        self.assertIn("async def update_coupon_usage_count", DB_COUPONS)
        self.assertIn("return await claim_coupon_slot(session, coupon_id)", DB_COUPONS)

    def test_бот_занимает_слот_до_выдачи(self):
        body = BOT_COUPONS[BOT_COUPONS.index("if not await claim_coupon_slot") :][:900]
        self.assertLess(body.index("claim_coupon_slot"), body.index("create_coupon_usage"))
        self.assertIn("await release_coupon_slot(session, coupon.id)", body)

    def test_сайт_занимает_слот_до_начисления(self):
        body = SITE_COUPONS[SITE_COUPONS.index("if not await claim_coupon_slot") :][:700]
        self.assertLess(body.index("claim_coupon_slot"), body.index("update_balance"))
        self.assertLess(body.index("create_coupon_usage"), body.index("update_balance"))
        self.assertIn("await release_coupon_slot", body)


class BulkWebNotifyTests(unittest.TestCase):
    def test_веб_уведомление_идёт_отдельной_сессией(self):
        """Сбой уведомления не должен ронять остаток массовой операции."""
        body = BULK[BULK.index("async def _notify_reissue") :]
        body = body[: body.index("return delivered")]
        self.assertIn("async with async_session_maker() as notify_session:", body)
        self.assertIn("notify_web(\n                    notify_session,", body)
        self.assertNotIn("_notify_reissue(session,", BULK)

    def test_перевыпуск_уведомляет_и_кабинет(self):
        body = BULK[BULK.index("async def _notify_reissue") :]
        body = body[: body.index("async def bulk_reissue_link")]
        self.assertIn("bot.send_message", body)
        self.assertIn("notify_web", body)

    def test_телеграм_только_реальным_чатам(self):
        body = BULK[BULK.index("async def _notify_reissue") :]
        self.assertIn("if tg_id and int(tg_id) > 0:", body)

    def test_веб_уведомление_не_ограничено_знаком_id(self):
        body = BULK[BULK.index("async def _notify_reissue") :]
        body = body[: body.index("return delivered")]
        web_gate = body[body.index("notify_web") - 200 : body.index("notify_web")]
        self.assertNotIn("> 0", web_gate, "у веб-клиента tg_id отрицательный")

    def test_обе_доставки_не_роняют_массовую_операцию(self):
        body = BULK[BULK.index("async def _notify_reissue") :]
        body = body[: body.index("return delivered")]
        self.assertEqual(body.count("except Exception as e:"), 2)


if __name__ == "__main__":
    unittest.main()
