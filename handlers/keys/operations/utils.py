def split_by_panel(servers: list) -> tuple[list, list]:
    xui = []
    remna = []
    for s in servers:
        pt = str(s.get("panel_type", "3x-ui")).lower()
        if pt == "3x-ui":
            xui.append(s)
        elif pt == "remnawave":
            remna.append(s)
    return xui, remna


def bytes_from_gb(total_gb: int) -> int:
    return total_gb * 1024 * 1024 * 1024 if total_gb else 0


def is_plan_vless(plan) -> bool:
    if plan is None:
        return False
    if isinstance(plan, dict):
        return bool(plan.get("vless"))
    return bool(getattr(plan, "vless", False))


def score_vless_url(url: str) -> int:
    u = url.lower()
    if not u.startswith("vless://"):
        return -1
    s = 0
    if "security=reality" in u and "type=tcp" in u:
        s += 4
    if "type=ws" in u and "security=tls" in u:
        s += 3
    if "security=tls" in u and "type=tcp" in u:
        s += 2
    if "type=ws" in u:
        s += 1
    return s


def norm_name(x: str | None) -> str:
    return (x or "").strip().lower()


def unique_by_api_url(servers: list) -> list:
    seen = set()
    out = []
    for s in servers or []:
        url = (s.get("api_url") or "").rstrip("/")
        if url and url not in seen:
            seen.add(url)
            out.append(s)
    return out
