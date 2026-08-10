import asyncio
import os
import sys
import types
import warnings

from pathlib import Path


warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _Any:
    def __getattr__(self, name):
        return _Any()

    def __call__(self, *args, **kwargs):
        return None


class _StubLogger(types.ModuleType):
    def __getattr__(self, name):
        return _Any()


sys.modules.setdefault("logger", _StubLogger("logger"))
sys.modules.setdefault("settings.config", types.SimpleNamespace(PROCESS_POOL_SIZE=1, EXECUTOR_POOL_SIZE=2))

from core.executor import get_process_pool, run_cpu  # noqa: E402
from utils.cpu_tasks import generate_qr_file  # noqa: E402


async def main() -> None:
    first, second = "/tmp/_solo_pool_1.png", "/tmp/_solo_pool_2.png"
    try:
        await run_cpu(generate_qr_file, "https://example.com", first)
        if not os.path.exists(first):
            print("NO_FIRST_FILE")
            return
        for pid in list(get_process_pool()._processes.keys()):
            os.kill(pid, 9)
        await asyncio.sleep(1.0)
        await run_cpu(generate_qr_file, "https://example.com/2", second)
        print("RECOVERED" if os.path.exists(second) else "NO_SECOND_FILE")
    except Exception as exc:
        print("BROKEN:" + type(exc).__name__)
    finally:
        for path in (first, second):
            if os.path.exists(path):
                os.remove(path)


if __name__ == "__main__":
    asyncio.run(main())
