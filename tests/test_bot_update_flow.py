import re
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CLI = (ROOT / "cli_launcher.py").read_text(encoding="utf-8")
BOT = (ROOT / "handlers" / "admin" / "management" / "update_handler.py").read_text(encoding="utf-8")
KEYBOARD = (ROOT / "handlers" / "admin" / "management" / "keyboard.py").read_text(encoding="utf-8")
PACKAGE = (ROOT / "handlers" / "admin" / "management" / "__init__.py").read_text(encoding="utf-8")
APP = (ROOT / "core" / "app.py").read_text(encoding="utf-8")
RPC = (ROOT / "core" / "rpc.py").read_text(encoding="utf-8")


def _extract(source: str, pattern: str, namespace: dict) -> None:
    found = re.search(pattern, source, re.S | re.M)
    assert found, f"не найдено: {pattern}"
    exec(found.group(0), namespace)


def _load_cli():
    """cli_launcher тянет rpc и сеть — разбираемые функции чистые, берём их из файла."""
    namespace: dict = {"re": re}
    for pattern in (
        r"^_AUTO_FILE_MARKERS = \(.*?\n\)\n",
        r"^_AUTO_STOP_MARKERS = \(.*?\n\)\n",
        r"^def _auto_answer\(.*?\n    return True\n",
        r"^def _parse_cli_args\(.*?\n    return job\n",
    ):
        _extract(CLI, pattern, namespace)
    namespace["_AUTO_YES"] = False
    namespace["_AUTO_OVERWRITE"] = {}
    namespace["_AUTO_ABORT_REASON"] = ""
    return namespace


def _load_bot():
    namespace: dict = {"re": re, "sys": type("S", (), {"executable": "/py"}), "LAUNCHER": "/bot/cli_launcher.py"}
    _extract(BOT, r"^FILE_OPTIONS: .*?\n\)\n", namespace)
    _extract(BOT, r"^def parse_tag_version\(.*?\n    return tuple\(parts\) if parts else \(0,\)\n", namespace)
    _extract(BOT, r"^def build_update_command\(.*?\n    return command\n", namespace)
    return namespace


CLI_NS = _load_cli()
BOT_NS = _load_bot()


class ArgsTests(unittest.TestCase):
    def setUp(self):
        self.parse = CLI_NS["_parse_cli_args"]

    def test_обычный_запуск_остаётся_интерактивным(self):
        self.assertIsNone(self.parse([]))
        self.assertIsNone(self.parse(["--help"]))
        self.assertIsNone(self.parse(["мусор"]))

    def test_канал_и_версия(self):
        job = self.parse(["--update", "release", "--tag", "v6.1"])
        self.assertEqual(job["channel"], "release")
        self.assertEqual(job["tag"], "v6.1")

    def test_канал_по_умолчанию_релиз(self):
        self.assertEqual(self.parse(["--update"])["channel"], "release")

    def test_флаги_разрешают_перезапись(self):
        job = self.parse(["--update", "beta", "--with-img", "--with-redis-cache"])
        self.assertEqual(job["channel"], "beta")
        self.assertEqual(job["overwrite"], {"img": True, "redis_cache": True})

    def test_без_флагов_ничего_не_перезаписывается(self):
        self.assertEqual(self.parse(["--update", "beta"])["overwrite"], {})

    def test_адресат_отчёта(self):
        self.assertEqual(self.parse(["--update", "release", "--notify", "42"])["notify"], 42)
        self.assertEqual(self.parse(["--update", "release", "--notify", "x"])["notify"], 0)
        self.assertEqual(self.parse(["--update", "release"])["notify"], 0)


class AutoAnswerTests(unittest.TestCase):
    def _answer(self, message, overwrite=None):
        namespace = _load_cli()
        namespace["_AUTO_YES"] = True
        namespace["_AUTO_OVERWRITE"] = dict(overwrite or {})
        return namespace["_auto_answer"](message), namespace

    def test_интерактивный_режим_не_отвечает_сам(self):
        self.assertIsNone(CLI_NS["_auto_answer"]("Продолжить обновление?"))

    def test_файлы_защищены_пока_нет_флага(self):
        for message in ("Обновлять файл buttons.py?", "Обновлять папку img?", "Обновлять файл core/redis_cache.py?"):
            self.assertIs(self._answer(message)[0], False, message)

    def test_флаг_разрешает_перезапись(self):
        self.assertIs(self._answer("Обновлять папку img?", {"img": True})[0], True)
        self.assertIs(self._answer("Обновлять файл buttons.py?", {"buttons": True})[0], True)
        self.assertIs(self._answer("Обновлять файл core/redis_cache.py?", {"redis_cache": True})[0], True)

    def test_флаг_картинок_не_влияет_на_кнопки(self):
        self.assertIs(self._answer("Обновлять файл buttons.py?", {"img": True})[0], False)

    def test_обычные_подтверждения_да(self):
        self.assertIs(self._answer("Вы точно хотите продолжить?")[0], True)
        self.assertIs(self._answer("Установить v6.1?")[0], True)

    def test_без_бэкапа_не_обновляемся(self):
        answer, namespace = self._answer("[warn]Бэкап не создан. Продолжить обновление БЕЗ бэкапа?[/warn]")
        self.assertIs(answer, False)
        self.assertTrue(namespace["_AUTO_ABORT_REASON"])

    def test_неполный_конфиг_останавливает_обновление(self):
        answer, namespace = self._answer("[yellow]Всё равно продолжить обновление?[/yellow]")
        self.assertIs(answer, False)
        self.assertIn("config", namespace["_AUTO_ABORT_REASON"])


class UnattendedFlowTests(unittest.TestCase):
    def test_отказ_докладывается_как_отмена_а_не_как_успех(self):
        body = CLI[CLI.index("def run_unattended_update") :]
        body = body[: body.index("\ndef _parse_cli_args")]
        self.assertIn('_write_update_report("skipped", _AUTO_ABORT_REASON, channel, tag, notify)', body)
        self.assertLess(body.index("_AUTO_ABORT_REASON:"), body.index('_write_update_report("ok"'))

    def test_без_версии_ставится_самая_свежая_а_не_вопрос_в_пустой_stdin(self):
        body = CLI[CLI.index("def update_from_release") :]
        body = body[: body.index("\nWEB_IMAGE_REPO")]
        auto = body.index("if _AUTO_YES:")
        prompt = body.index("selected = safe_prompt(")
        self.assertLess(auto, prompt, "автоветка обязана вернуться до интерактивного выбора версии")
        self.assertIn("_AUTO_TAG or tag_names[-1]", body)

    def test_отчёт_пишется_в_один_и_тот_же_файл(self):
        self.assertIn('UPDATE_REPORT_FILE = os.path.join(PROJECT_DIR, ".update_report.json")', CLI)
        self.assertIn('REPORT_FILE = os.path.join(PROJECT_DIR, ".update_report.json")', BOT)


class BotCommandTests(unittest.TestCase):
    def setUp(self):
        self.build = BOT_NS["build_update_command"]

    def test_релиз_с_версией(self):
        command = self.build({"channel": "release", "tag": "v6.1", "overwrite": {}}, 7)
        self.assertEqual(command[1:], ["/bot/cli_launcher.py", "--update", "release", "--tag", "v6.1", "--notify", "7"])

    def test_последняя_версия_без_тега(self):
        command = self.build({"channel": "release", "tag": "", "overwrite": {}}, 7)
        self.assertNotIn("--tag", command)

    def test_бета_не_принимает_тег(self):
        command = self.build({"channel": "beta", "tag": "v6.1", "overwrite": {}}, 7)
        self.assertNotIn("--tag", command)

    def test_выключенные_тумблеры_не_дают_флагов(self):
        command = self.build({"channel": "release", "overwrite": {"img": False}}, 1)
        self.assertFalse([item for item in command if item.startswith("--with-")])

    def test_включённые_тумблеры_дают_флаги_кли(self):
        command = self.build({"channel": "release", "overwrite": {"img": True, "redis_cache": True}}, 1)
        self.assertIn("--with-img", command)
        self.assertIn("--with-redis-cache", command)

    def test_флаги_бота_и_кли_совпадают(self):
        known = set(re.search(r"elif item in \((.*?)\):", CLI, re.S).group(1).replace('"', "").split(", "))
        known = {item.strip() for item in known if item.strip()}
        for key, _title in BOT_NS["FILE_OPTIONS"]:
            self.assertIn(f"--with-{key.replace('_', '-')}", known)


class TagListTests(unittest.TestCase):
    def test_версии_сортируются_как_числа_а_не_как_строки(self):
        parse = BOT_NS["parse_tag_version"]
        tags = ["v4.0", "v4.10", "v4.2", "v6.1.1", "v6.1"]
        self.assertEqual(sorted(tags, key=parse), ["v4.0", "v4.2", "v4.10", "v6.1", "v6.1.1"])

    def test_мусорный_тег_не_ломает_разбор(self):
        self.assertEqual(BOT_NS["parse_tag_version"]("beta"), (0,))

    def test_старые_ветки_отсекаются_как_в_кли(self):
        self.assertIn("parse_tag_version(name)[0] >= MIN_MAJOR", BOT)
        self.assertIn("MIN_MAJOR = 4", BOT)


class WiringTests(unittest.TestCase):
    def test_кнопка_стоит_рядом_с_перезапуском(self):
        restart = KEYBOARD.index('action="restart"')
        update = KEYBOARD.index('action="update"')
        self.assertLess(restart, update)
        self.assertLess(update - restart, 200, "кнопка обновления должна идти следом за перезапуском")

    def test_тумблер_меняет_только_значок_а_не_ширину_кнопки(self):
        segment = BOT[BOT.index("for key, title in FILE_OPTIONS:") :][:400]
        self.assertIn("""text=f"{'♻️' if overwrite.get(key) else '🔒'} {title}",""", segment)
        for word in ("сохранить", "перезаписать"):
            self.assertNotIn(word, segment, "подпись кнопки не должна нести слова — они в описании")

    def test_итог_по_каждому_файлу_виден_в_описании(self):
        segment = BOT[BOT.index("def _screen_text") : BOT.index("async def _render")]
        outcome = "f\"{title}: {'♻️ перезапишем' if overwrite.get(key) else '🔒 сохраним'}\""
        self.assertIn(outcome, segment)
        self.assertIn('section("⚙️ Что ставим", *lines)', segment)

    def test_назад_есть_на_каждом_экране(self):
        screens = ("_build_kb", "choose_tag", "start_update")
        for name in screens:
            body = BOT[BOT.index(f"def {name}") :]
            body = body[: body.index("\n\n\n")]
            self.assertIn("build_admin_back_btn(", body, name)

    def test_раздел_подключён(self):
        self.assertIn("update_handler", PACKAGE)
        self.assertIn("from . import router", BOT)

    def test_права_проверяются_на_запуске_обновления(self):
        segment = BOT[BOT.index('F.action == "update_start"') :][:300]
        self.assertIn("HasPermission(PERM_MANAGEMENT)", segment)
        self.assertIn("IsAdminFilter()", segment)

    def test_отчёт_ждут_после_перезапуска(self):
        self.assertIn("await report_update_result(bot)", APP)
        self.assertIn("spawn(_watch_report(bot))", BOT)

    def test_обновлятель_переживает_смерть_бота(self):
        self.assertIn("start_new_session=True", BOT)
        self.assertIn("stdin=subprocess.DEVNULL", BOT)

    def test_под_systemd_обновлятель_уходит_в_свой_юнит(self):
        segment = BOT[BOT.index("def _systemd_command") : BOT.index("def _launch_detached")]
        self.assertIn('"--unit"', segment)
        self.assertIn('"--collect"', segment)
        self.assertIn('shutil.which("systemd-run")', segment)
        self.assertIn('["sudo", "-n", *prefix]', segment)

    def test_без_systemd_остаётся_обычный_запуск(self):
        segment = BOT[BOT.index("def _launch_detached") :][:900]
        self.assertLess(segment.index("subprocess.Popen"), segment.index("def ", segment.index("subprocess.Popen")))
        self.assertIn("if result.returncode == 0:\n            return", segment)


class SecretsTests(unittest.TestCase):
    def test_адрес_сборщика_настроек_живёт_в_rpc(self):
        self.assertIn("def get_settings_builder_url", RPC)
        self.assertIn("CONFIG_BUILDER_URL = get_settings_builder_url()", CLI)

    def test_адрес_нигде_не_написан_словами(self):
        for name, source in (("cli_launcher.py", CLI), ("update_handler.py", BOT)):
            self.assertNotIn("pocomacho", source, name)
            self.assertNotIn("solonetbot", source, name)

    def test_старый_rpc_принудительно_обновляется(self):
        self.assertIn('hasattr(core.rpc, "get_settings_builder_url")', CLI)


if __name__ == "__main__":
    unittest.main()
