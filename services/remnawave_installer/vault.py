import json
import os
import stat
import tempfile

from contextlib import suppress
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .types import InstallError


class SecretVault:
    def __init__(self, path: Path | str = "data/remnawave-installer.key") -> None:
        self.path = Path(path)

    def _cipher(self) -> Fernet:
        """Открывает отдельный ключ шифрования с закрытыми правами."""
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.path.exists():
            descriptor, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".installer-key-")
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(Fernet.generate_key())
                    stream.flush()
                    os.fsync(stream.fileno())
                with suppress(FileExistsError):
                    os.link(temporary, self.path)
            finally:
                os.unlink(temporary)
        try:
            descriptor = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                    raise InstallError(
                        "secrets", "Ключ установщика должен быть обычным файлом с правами 0600", retryable=False
                    )
                key = stream.read(256)
            return Fernet(key)
        except InstallError:
            raise
        except (OSError, ValueError) as error:
            raise InstallError("secrets", "Не удалось открыть ключ шифрования установщика", retryable=False) from error

    def seal(self, job_id: str, value: dict) -> str:
        """Шифрует снимок и связывает его с конкретной задачей."""
        payload = json.dumps({"job_id": job_id, "value": value}, ensure_ascii=False).encode()
        if len(payload) > 2_000_000:
            raise ValueError("Слишком большой снимок установки")
        return self._cipher().encrypt(payload).decode()

    def open(self, job_id: str, value: str | None) -> dict:
        """Проверяет целостность и принадлежность зашифрованного снимка."""
        if not value:
            return {}
        if not self.path.exists():
            raise InstallError(
                "secrets",
                "Ключ установщика потерян. Восстановите data/remnawave-installer.key из резервной копии",
                retryable=False,
            )
        try:
            payload = json.loads(self._cipher().decrypt(value.encode()))
            if payload.get("job_id") != job_id or not isinstance(payload.get("value"), dict):
                raise ValueError
            return payload["value"]
        except (InvalidToken, ValueError, TypeError, KeyError) as error:
            raise InstallError("secrets", "Не удалось расшифровать снимок установки", retryable=False) from error


vault = SecretVault()
