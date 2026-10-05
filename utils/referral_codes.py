import base64
import hashlib
import hmac
import re

from settings.config import API_TOKEN, WEBHOOK_SECRET_TOKEN


def _secret_bytes() -> bytes:
    seed = (WEBHOOK_SECRET_TOKEN or API_TOKEN or "solobot-referral").strip()
    return seed.encode("utf-8")


def _urlsafe_b64decode_nopad(value: str) -> bytes:
    normalized = value + "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(normalized.encode("ascii"))


def encode_referral_code(user_id: int) -> str:
    if int(user_id) <= 0:
        raise ValueError("user_id must be positive")
    raw = int(user_id).to_bytes(8, byteorder="big", signed=False)
    secret = _secret_bytes()
    mask = hmac.new(secret, b"ref-mask-v1", hashlib.sha256).digest()[:8]
    obfuscated = bytes(a ^ b for a, b in zip(raw, mask, strict=False))
    signature = hmac.new(secret, b"ref-sign-v1:" + obfuscated, hashlib.sha256).digest()[:6]
    payload = base64.urlsafe_b64encode(obfuscated + signature).decode("ascii").rstrip("=")
    return f"r1_{payload}"


def encode_partner_code(user_id: int) -> str:
    if int(user_id) <= 0:
        raise ValueError("user_id must be positive")
    raw = int(user_id).to_bytes(8, byteorder="big", signed=False)
    secret = _secret_bytes()
    mask = hmac.new(secret, b"partner-mask-v1", hashlib.sha256).digest()[:8]
    obfuscated = bytes(a ^ b for a, b in zip(raw, mask, strict=False))
    signature = hmac.new(secret, b"partner-sign-v1:" + obfuscated, hashlib.sha256).digest()[:6]
    payload = base64.urlsafe_b64encode(obfuscated + signature).decode("ascii").rstrip("=")
    return f"p1_{payload}"


def decode_referral_code(value: str | None) -> int | None:
    token = str(value or "").strip()
    if not token:
        return None
    if token.startswith("r1_"):
        encoded = token[3:]
        try:
            data = _urlsafe_b64decode_nopad(encoded)
        except Exception:
            return None
        if len(data) != 14:
            return None
        obfuscated, signature = data[:8], data[8:]
        secret = _secret_bytes()
        expected = hmac.new(secret, b"ref-sign-v1:" + obfuscated, hashlib.sha256).digest()[:6]
        if not hmac.compare_digest(signature, expected):
            return None
        mask = hmac.new(secret, b"ref-mask-v1", hashlib.sha256).digest()[:8]
        raw = bytes(a ^ b for a, b in zip(obfuscated, mask, strict=False))
        parsed = int.from_bytes(raw, byteorder="big", signed=False)
        return parsed if parsed > 0 else None
    if token.startswith("p1_"):
        return None
    match = re.fullmatch(r"\d+", token)
    if not match:
        return None
    parsed = int(match.group(0))
    return parsed if parsed > 0 else None


def decode_partner_code(value: str | None) -> int | None:
    token = str(value or "").strip()
    if not token:
        return None
    if token.startswith("p1_"):
        encoded = token[3:]
        try:
            data = _urlsafe_b64decode_nopad(encoded)
        except Exception:
            return None
        if len(data) != 14:
            return None
        obfuscated, signature = data[:8], data[8:]
        secret = _secret_bytes()
        expected = hmac.new(secret, b"partner-sign-v1:" + obfuscated, hashlib.sha256).digest()[:6]
        if not hmac.compare_digest(signature, expected):
            return None
        mask = hmac.new(secret, b"partner-mask-v1", hashlib.sha256).digest()[:8]
        raw = bytes(a ^ b for a, b in zip(obfuscated, mask, strict=False))
        parsed = int.from_bytes(raw, byteorder="big", signed=False)
        return parsed if parsed > 0 else None
    if token.startswith("r1_"):
        return decode_referral_code(token)
    match = re.fullmatch(r"\d+", token)
    if not match:
        return None
    parsed = int(match.group(0))
    return parsed if parsed > 0 else None
