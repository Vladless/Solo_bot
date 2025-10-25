import os
import subprocess
import time

from functools import lru_cache

from logger import logger


_last_check_time = 0
_last_git_info = ""


def _get_git_commit_number_uncached() -> str:
    repo_url = "https://github.com/Vladless/Solo_bot"
    cwd = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    if not os.path.isdir(os.path.join(cwd, ".git")):
        cwd = "/root/Solo_bot"
        logger.info(f"[Git] .git не найден в текущем каталоге, используем {cwd}")

    env = os.environ.copy()
    env["GIT_DIR"] = os.path.join(cwd, ".git")
    env["GIT_WORK_TREE"] = cwd

    try:
        local_number = (
            subprocess.check_output(["git", "rev-list", "--count", "HEAD"], cwd=cwd, env=env).decode().strip()
        )
        local_hash = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=cwd, env=env).decode().strip()
        try:
            branch = (
                subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd, env=env).decode().strip()
            )
            if branch == "HEAD":
                describe = (
                    subprocess.check_output(
                        ["git", "describe", "--tags", "--exact-match"],
                        cwd=cwd,
                        env=env,
                        stderr=subprocess.DEVNULL,
                    )
                    .decode()
                    .strip()
                )
                branch = "main" if describe.startswith("v") or "release" in describe.lower() else "dev"
        except Exception:
            branch = "dev"

    except Exception as e:
        logger.error(f"[Git] Ошибка при получении локального коммита: {e}")
        return f"\n(Требуется обновление через CLI (команда <code>sudo solobot</code>): {e})"

    try:
        subprocess.check_output(["git", "fetch", "origin"], cwd=cwd, env=env)
        remote_commit = subprocess.check_output(
            ["git", "ls-remote", "origin", f"refs/heads/{branch}"], cwd=cwd, env=env
        ).decode()
        remote_hash = remote_commit.split()[0]

        remote_number = (
            subprocess.check_output(["git", "rev-list", "--count", remote_hash], cwd=cwd, env=env).decode().strip()
        )

        if local_hash == remote_hash:
            logger.debug("[Git] Локальная версия актуальна")
            return "\n(Актуальная версия)"

        return (
            f'\n(commit <a href="{repo_url}/commit/{local_hash}">'
            f"#{local_number}</a> / actual commit "
            f'<a href="{repo_url}/commit/{remote_hash}">#{remote_number}</a>)'
        )
    except Exception as e:
        logger.error(f"[Git] Ошибка при получении удалённого коммита: {e}")
        return "\n(Требуется обновление через CLI, команда <code>sudo solobot</code>)"


@lru_cache(maxsize=1)
def _cached_git_info() -> str:
    return _get_git_commit_number_uncached()


def get_git_commit_number() -> str:
    global _last_check_time, _last_git_info
    now = time.time()
    if now - _last_check_time > 3600:
        _last_check_time = now
        _cached_git_info.cache_clear()
        _last_git_info = _cached_git_info()
    return _last_git_info


def get_version() -> str:
    return f"v.5-Release {get_git_commit_number()}"
