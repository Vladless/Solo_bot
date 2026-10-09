import hashlib


def traffic_resource_id(panel_type: str, api_url: str, inbound_id=None, email: str = "") -> str:
    """Возвращает стабильный идентификатор независимого счётчика."""
    source = f"{panel_type}|{api_url.rstrip('/')}|{inbound_id or ''}|{email}"
    return hashlib.sha256(source.encode()).hexdigest()
