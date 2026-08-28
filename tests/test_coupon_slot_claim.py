import unittest

from sqlalchemy import and_, case, create_engine, func, or_, update
from sqlalchemy.orm import sessionmaker

import database  # noqa: F401 — модели не поднимутся первым импортом: цикл через core.bootstrap

from database.models import Coupon


def _claim(session, coupon_id: int) -> bool:
    """Копия условия из database.coupons.claim_coupon_slot на синхронном движке."""
    used = func.coalesce(Coupon.usage_count, 0)
    result = session.execute(
        update(Coupon)
        .where(Coupon.id == coupon_id, or_(Coupon.usage_limit.is_(None), used < Coupon.usage_limit))
        .values(
            usage_count=used + 1,
            is_used=case(
                (and_(Coupon.usage_limit.isnot(None), used + 1 >= Coupon.usage_limit), True),
                else_=False,
            ),
        )
    )
    return bool(result.rowcount)


def _release(session, coupon_id: int) -> None:
    used = func.coalesce(Coupon.usage_count, 0)
    session.execute(
        update(Coupon)
        .where(Coupon.id == coupon_id, used > 0)
        .values(
            usage_count=used - 1,
            is_used=case(
                (and_(Coupon.usage_limit.isnot(None), used - 1 >= Coupon.usage_limit), True),
                else_=False,
            ),
        )
    )


class CouponSlotBehaviourTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Coupon.__table__.create(self.engine)
        self.Session = sessionmaker(self.engine)
        self.addCleanup(self.engine.dispose)

    def _coupon(self, session, **kw):
        defaults = {"code": f"c{kw['id']}", "amount": 10, "usage_limit": 3, "usage_count": 0}
        defaults.update(kw)
        session.add(Coupon(**defaults))
        session.commit()

    def _state(self, session, coupon_id):
        coupon = session.get(Coupon, coupon_id)
        session.refresh(coupon)
        return coupon.usage_count, coupon.is_used

    def test_свободный_слот_занимается(self):
        with self.Session() as s:
            self._coupon(s, id=1, usage_count=1)
            self.assertTrue(_claim(s, 1))
            s.commit()
            self.assertEqual(self._state(s, 1), (2, False))

    def test_последний_слот_закрывает_купон(self):
        with self.Session() as s:
            self._coupon(s, id=1, usage_count=2)
            self.assertTrue(_claim(s, 1))
            s.commit()
            self.assertEqual(self._state(s, 1), (3, True))

    def test_за_лимит_не_пускает_и_счётчик_не_растёт(self):
        with self.Session() as s:
            self._coupon(s, id=1, usage_count=3)
            self.assertFalse(_claim(s, 1))
            s.commit()
            self.assertEqual(self._state(s, 1)[0], 3, "счётчик не должен превышать лимит")

    def test_двое_за_последний_слот_проходит_один(self):
        with self.Session() as s:
            self._coupon(s, id=1, usage_count=2)
            first = _claim(s, 1)
            second = _claim(s, 1)
            s.commit()
            self.assertEqual((first, second), (True, False))
            self.assertEqual(self._state(s, 1)[0], 3)

    def test_безлимитный_купон_остаётся_рабочим(self):
        """usage_limit nullable: сравнение с NULL сделало бы купон навсегда неактивируемым."""
        with self.Session() as s:
            self._coupon(s, id=1, usage_limit=None, usage_count=0)
            self.assertTrue(_claim(s, 1))
            s.commit()
            self.assertEqual(self._state(s, 1), (1, False))

    def test_пустой_счётчик_не_ломает_занятие(self):
        with self.Session() as s:
            self._coupon(s, id=1, usage_limit=3, usage_count=None)
            self.assertTrue(_claim(s, 1))
            s.commit()
            self.assertEqual(self._state(s, 1), (1, False))

    def test_оба_поля_пустые(self):
        with self.Session() as s:
            self._coupon(s, id=1, usage_limit=None, usage_count=None)
            self.assertTrue(_claim(s, 1))
            s.commit()
            self.assertEqual(self._state(s, 1), (1, False))

    def test_возврат_слота_освобождает_купон(self):
        with self.Session() as s:
            self._coupon(s, id=1, usage_count=2)
            _claim(s, 1)
            s.commit()
            self.assertEqual(self._state(s, 1), (3, True))
            _release(s, 1)
            s.commit()
            self.assertEqual(self._state(s, 1), (2, False), "купон снова доступен")

    def test_возврат_не_уводит_счётчик_в_минус(self):
        with self.Session() as s:
            self._coupon(s, id=1, usage_count=0)
            _release(s, 1)
            s.commit()
            self.assertEqual(self._state(s, 1)[0], 0)

    def test_условие_в_тестах_совпадает_с_боевым(self):
        from pathlib import Path

        src = (Path(__file__).resolve().parent.parent / "database" / "coupons.py").read_text(encoding="utf-8")
        body = src[src.index("async def claim_coupon_slot") : src.index("async def release_coupon_slot")]
        self.assertIn("or_(Coupon.usage_limit.is_(None), used < Coupon.usage_limit)", body)
        self.assertIn("used = func.coalesce(Coupon.usage_count, 0)", body)
        self.assertIn("and_(Coupon.usage_limit.isnot(None), used + 1 >= Coupon.usage_limit)", body)


if __name__ == "__main__":
    unittest.main()
