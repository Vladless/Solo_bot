import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULTS = (ROOT / "core" / "defaults.py").read_text(encoding="utf-8")
CONFIG = (ROOT / "core" / "settings" / "legal_config.py").read_text(encoding="utf-8")
BOOTSTRAP = (ROOT / "core" / "bootstrap.py").read_text(encoding="utf-8")
LEGAL = (ROOT / "handlers" / "legal.py").read_text(encoding="utf-8")
START = (ROOT / "handlers" / "start.py").read_text(encoding="utf-8")
ADMIN = (ROOT / "handlers" / "admin" / "settings" / "settings_legal.py").read_text(encoding="utf-8")
KEYBOARD = (ROOT / "handlers" / "admin" / "settings" / "keyboard.py").read_text(encoding="utf-8")
ROUTERS = (ROOT / "handlers" / "__init__.py").read_text(encoding="utf-8")
ADMIN_ROUTERS = (ROOT / "handlers" / "admin" / "settings" / "__init__.py").read_text(encoding="utf-8")
MODELS = (ROOT / "database" / "models" / "users.py").read_text(encoding="utf-8")
MIGRATIONS = (ROOT / "database" / "migrations" / "schema_upgrade.py").read_text(encoding="utf-8")


class LegalDocsTests(unittest.TestCase):
    def test_тумблер_и_три_ссылки_лежат_в_одной_группе(self):
        for key in ("LEGAL_DOCS_ENABLED", "LEGAL_PRIVACY_URL", "LEGAL_TERMS_URL", "LEGAL_OFFER_URL"):
            self.assertIn(f'"{key}"', DEFAULTS)
        self.assertIn('register_runtime_config("LEGAL_CONFIG", LEGAL_CONFIG)', CONFIG)

    def test_конфиг_поднимается_при_старте(self):
        self.assertIn("await load_legal_config(session)", BOOTSTRAP)

    def test_выключенный_тумблер_гасит_раздел(self):
        self.assertIn('if not bool(LEGAL_CONFIG.get("LEGAL_DOCS_ENABLED", False)):\n        return False', CONFIG)

    def test_без_ссылок_раздел_не_показывается(self):
        self.assertIn("return any(str(LEGAL_CONFIG.get(key) or \"\").strip() for key in LEGAL_DOC_KEYS)", CONFIG)

    def test_кнопки_строятся_только_для_заполненных_ссылок(self):
        self.assertIn("if not is_legal_enabled():\n        return []", LEGAL)
        self.assertIn("if url:\n            buttons.append", LEGAL)

    def test_гейт_стоит_до_показа_меню(self):
        gate = START.index("legal_gate_passed")
        menu = START.index("await show_start_menu(message, admin, session, trial=trial, key_count=key_count)")
        self.assertLess(gate, menu, "экран согласия должен отсекать показ меню")

    def test_гейт_срабатывает_после_создания_пользователя(self):
        created = START.index("await add_user(session=session, **user_data)")
        gate = START.index("legal_gate_passed")
        self.assertLess(created, gate, "согласие пишется в существующую строку пользователя")

    def test_сбой_чтения_согласия_не_блокирует_клиента(self):
        self.assertIn("except Exception as exc:", LEGAL)
        segment = LEGAL[LEGAL.index("async def legal_gate_passed") :]
        self.assertIn("return True", segment.split("await show_legal_gate")[0])

    def test_кнопки_документов_есть_в_меню_о_сервисе(self):
        about = START[START.index("async def handle_about_vpn") :]
        self.assertIn("legal_doc_buttons()", about)

    def test_согласие_сохраняется_и_ведёт_дальше(self):
        self.assertIn("values(legal_accepted_at=datetime.utcnow())", LEGAL)
        self.assertIn("await process_start_logic(", LEGAL)

    def test_колонка_и_миграция_на_месте(self):
        self.assertIn("legal_accepted_at = Column(DateTime, nullable=True)", MODELS)
        self.assertIn("_migration_v50_users_legal_accepted_at", MIGRATIONS)
        self.assertIn("ALTER TABLE users ADD COLUMN legal_accepted_at TIMESTAMP", MIGRATIONS)

    def test_миграция_идемпотентна(self):
        segment = MIGRATIONS[MIGRATIONS.index("async def _migration_v50_users_legal_accepted_at") :][:600]
        self.assertIn('if await _column_exists(conn, "users", "legal_accepted_at"):\n        return', segment)

    def test_админ_задаёт_ссылки_на_все_три_документа(self):
        for key in ("LEGAL_PRIVACY_URL", "LEGAL_TERMS_URL", "LEGAL_OFFER_URL"):
            self.assertIn(key, ADMIN)
        self.assertIn("LegalSettingsState.waiting_for_url", ADMIN)

    def test_ссылка_проверяется_перед_сохранением(self):
        self.assertIn('raw.startswith(("http://", "https://"))', ADMIN)
        self.assertIn('if raw == "-":', ADMIN)

    def test_раздел_есть_в_меню_настроек_и_подключён(self):
        self.assertIn('action="settings_legal"', KEYBOARD)
        self.assertIn("router.include_router(settings_legal_router)", ADMIN_ROUTERS)
        self.assertIn("legal_router,", ROUTERS)


if __name__ == "__main__":
    unittest.main()
