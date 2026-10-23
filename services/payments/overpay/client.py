from __future__ import annotations

import os
import ssl
import subprocess
import tempfile

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import aiohttp

from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, pkcs12

from logger import logger
from settings import texts
from settings.config import (
    OVERPAY_API_URL,
    OVERPAY_CERT_PASSWORD,
    OVERPAY_CERT_PATH,
    OVERPAY_PASSWORD,
    OVERPAY_USERNAME,
)


_ssl_context: ssl.SSLContext | None = None


@dataclass(frozen=True)
class APIResult:
    data: dict | None = None
    unknown: bool = False
    error: str | None = None


def api_url(path: str) -> str:
    """Собирает адрес API без повторного префикса."""
    base = str(OVERPAY_API_URL or "").strip().rstrip("/")
    if base.endswith("/api"):
        base = base[:-4]
    route = path.lstrip("/")
    if route.startswith("api/"):
        route = route[4:]
    return f"{base}/api/{route}"


async def request(
    method: str,
    path: str,
    *,
    payload: dict | None = None,
    params: dict | None = None,
    timeout_seconds: int = 30,
    before_send: Callable[[], Awaitable[bool]] | None = None,
) -> APIResult:
    """Выполняет один запрос с клиентским сертификатом."""
    if not all(str(value or "").strip() for value in (OVERPAY_API_URL, OVERPAY_USERNAME, OVERPAY_PASSWORD)):
        return APIResult(error=texts.OVERPAY_API_NOT_CONFIGURED)
    context = _build_ssl_context()
    if context is None:
        return APIResult(error=texts.OVERPAY_API_CERT_NOT_CONFIGURED)
    timeout = aiohttp.ClientTimeout(total=max(1, int(timeout_seconds)), connect=10)
    auth = aiohttp.BasicAuth(OVERPAY_USERNAME, OVERPAY_PASSWORD)
    try:
        connector = aiohttp.TCPConnector(ssl=context)
        async with aiohttp.ClientSession(timeout=timeout, connector=connector, auth=auth) as session:
            if before_send is not None and not await before_send():
                return APIResult(error=texts.OVERPAY_API_DISPATCH_CANCELED)
            async with session.request(
                method,
                api_url(path),
                json=payload,
                params=params,
                headers={"Accept": "application/json"},
            ) as response:
                if response.status >= 500 or response.status in {408, 429}:
                    return APIResult(unknown=True, error=texts.OVERPAY_API_RESULT_UNKNOWN)
                if response.status >= 400:
                    logger.warning(
                        "OverPay API: запрос отклонён, method={}, path={}, HTTP={}", method, path, response.status
                    )
                    return APIResult(error=texts.OVERPAY_API_REQUEST_REJECTED)
                try:
                    data = await response.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError):
                    return APIResult(unknown=True, error=texts.OVERPAY_API_RESPONSE_INVALID)
                if not isinstance(data, dict):
                    return APIResult(unknown=True, error=texts.OVERPAY_API_RESPONSE_INVALID)
                return APIResult(data=data)
    except (aiohttp.ClientError, TimeoutError):
        return APIResult(unknown=True, error=texts.OVERPAY_API_RESULT_UNKNOWN)


def _resolve_cert_path(path: str) -> str:
    if os.path.isabs(path):
        return path
    base_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(base_dir, "..", "..", ".."))
    return os.path.join(project_root, path)


def _pem_from_cryptography(p12_data: bytes, password: bytes | None) -> bytes | None:
    """Преобразует PKCS12 в PEM через cryptography."""
    private_key, certificate, additional = pkcs12.load_key_and_certificates(p12_data, password)
    if private_key is None or certificate is None:
        logger.error("[Overpay] В .p12 нет приватного ключа или сертификата")
        return None
    bundle = certificate.public_bytes(Encoding.PEM)
    for ca_cert in additional or []:
        bundle += ca_cert.public_bytes(Encoding.PEM)
    bundle += private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    return bundle


def _pem_from_openssl(path: str, password: str) -> bytes | None:
    """Преобразует PKCS12 в PEM через OpenSSL."""
    env = {**os.environ, "OVERPAY_P12_PASS": password}
    for extra in (["-legacy"], []):
        try:
            result = subprocess.run(
                ["openssl", "pkcs12", "-in", path, "-nodes", "-passin", "env:OVERPAY_P12_PASS", *extra],
                capture_output=True,
                timeout=20,
                env=env,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            logger.warning(f"[Overpay] openssl недоступен: {error}")
            return None
        if result.returncode == 0 and b"BEGIN" in result.stdout:
            logger.info("[Overpay] Сертификат разобран через openssl{}".format(" -legacy" if extra else ""))
            return result.stdout
    logger.error("[Overpay] openssl тоже не смог разобрать .p12")
    return None


def _build_ssl_context() -> ssl.SSLContext | None:
    global _ssl_context
    if _ssl_context is not None:
        return _ssl_context

    cert_path = (OVERPAY_CERT_PATH or "").strip()
    if not cert_path:
        logger.error("[Overpay] OVERPAY_CERT_PATH не задан")
        return None

    resolved = _resolve_cert_path(cert_path)
    if not os.path.isfile(resolved):
        logger.error(f"[Overpay] Файл сертификата не найден: {resolved}")
        return None

    try:
        with open(resolved, "rb") as cert_file:
            p12_data = cert_file.read()

        raw_password = OVERPAY_CERT_PASSWORD or ""
        password = raw_password.encode("utf-8") or None

        try:
            pem_bundle = _pem_from_cryptography(p12_data, password)
        except Exception as error:
            logger.warning(f"[Overpay] Штатный разбор .p12 не удался ({error}), пробую openssl")
            pem_bundle = None

        if pem_bundle is None:
            pem_bundle = _pem_from_openssl(resolved, raw_password)
        if pem_bundle is None:
            return None

        context = ssl.create_default_context()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pem")
        try:
            tmp.write(pem_bundle)
            tmp.flush()
            tmp.close()
            context.load_cert_chain(tmp.name)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

        _ssl_context = context
        logger.info("[Overpay] SSL-контекст с клиентским сертификатом успешно построен")
        return context
    except Exception as e:
        logger.error(f"[Overpay] Не удалось загрузить сертификат .p12: {e}")
        return None
