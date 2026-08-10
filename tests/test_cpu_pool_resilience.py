import ast
import subprocess
import sys
import types
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class _Any:
    def __getattr__(self, name):
        return _Any()

    def __call__(self, *args, **kwargs):
        return None


class _StubLogger(types.ModuleType):
    def __getattr__(self, name):
        return _Any()


sys.modules.setdefault("logger", _StubLogger("logger"))
sys.modules.setdefault(
    "settings.config", types.SimpleNamespace(PROCESS_POOL_SIZE=1, EXECUTOR_POOL_SIZE=2)
)


class ProcessPoolResilienceTests(unittest.TestCase):
    def test_пул_переживает_смерть_воркера(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "tests" / "pool_recovery_scenario.py")],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            timeout=120,
        )
        self.assertIn(
            "RECOVERED",
            result.stdout,
            f"пул не восстановился: {result.stdout.strip() or result.stderr.strip()[-200:]}",
        )


class CpuTaskImportWeightTests(unittest.TestCase):
    def test_модуль_задач_не_тянет_приложение(self):
        source = (ROOT / "utils" / "cpu_tasks.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                roots.add(node.module.split(".")[0])

        app_packages = {
            "api", "core", "database", "handlers", "services", "settings",
            "panels", "middlewares", "utils", "modules", "logger", "bot",
        }
        self.assertEqual(roots & app_packages, set(), f"модуль тянет приложение: {sorted(roots & app_packages)}")

    def test_все_задачи_пула_берутся_из_этого_модуля(self):
        offenders = []
        for path in ROOT.rglob("*.py"):
            parts = set(path.parts)
            if parts & {"venv", "node_modules", "tests", ".git"}:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            allowed = {
                name
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module == "utils.cpu_tasks"
                for name in (alias.asname or alias.name for alias in node.names)
            }
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if getattr(func, "id", None) != "run_cpu":
                    continue
                target = node.args[0] if node.args else None
                name = getattr(target, "id", None)
                if name and name not in allowed:
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} → {name}")

        self.assertEqual(offenders, [], "задачи пула вне utils/cpu_tasks.py: " + "; ".join(offenders))


if __name__ == "__main__":
    unittest.main()
