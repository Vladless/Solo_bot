_SPECIAL_GROUP_CODES = frozenset({
    "trial",
    "discounts",
    "discounts_max",
    "cold_discounts",
    "cold_discounts_max",
    "gifts",
})


def _tariff_id(value: object) -> int | None:
    """Возвращает корректный ID тарифа."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def _has_tariff_binding(server: dict, tariff_id: int) -> bool:
    """Проверяет прямую привязку сервера к тарифу."""
    bindings = server.get("tariff_ids")
    if not isinstance(bindings, list | tuple | set | frozenset):
        return False
    return any(_tariff_id(value) == tariff_id for value in bindings)


def _has_legacy_binding(server: dict, subgroup: str, group_code: str | None = None) -> bool:
    """Проверяет старую привязку без прямых тарифов."""
    server_group = server.get("tariff_group")
    if server_group is not None and server_group != group_code:
        return False
    tariffs = server.get("tariff_ids")
    if tariffs is not None and (not isinstance(tariffs, list | tuple | set | frozenset) or tariffs):
        return False
    subgroups = server.get("tariff_subgroups")
    return isinstance(subgroups, list | tuple | set | frozenset) and subgroup in subgroups


def _group_only_servers(cluster_servers: list[dict], tariff: dict, scope: list[dict]) -> list[dict]:
    """Учитывает назначенную группу без точечных ограничений."""
    group = tariff.get("group_code")
    if not isinstance(group, str) or not group.strip():
        return []
    group_servers = [server for server in [*scope, *cluster_servers] if server.get("tariff_group") == group]
    for server in group_servers:
        for field in ("tariff_ids", "tariff_subgroups"):
            bindings = server.get(field)
            if bindings is not None and (not isinstance(bindings, list | tuple | set | frozenset) or bindings):
                return []
    return [server for server in cluster_servers if server.get("tariff_group") == group]


def is_server_configured(server: dict) -> bool:
    """Проверяет обязательные параметры панели и инбаунда."""
    api_url = server.get("api_url")
    if not isinstance(api_url, str) or not api_url.strip():
        return False
    panel_type = str(server.get("panel_type") or "").lower()
    inbound_id = server.get("inbound_id")
    if panel_type == "remnawave":
        return isinstance(inbound_id, str) and bool(inbound_id.strip())
    if panel_type == "3x-ui":
        return _tariff_id(inbound_id) is not None
    return False


def resolve_tariff_servers(
    cluster_servers: list[dict],
    tariff: dict | None,
    *,
    binding_scope: list[dict] | None = None,
    selected_server: str | None = None,
    is_trial: bool = False,
) -> list[dict]:
    """Возвращает только серверы с явным разрешением тарифа."""
    if not tariff:
        return []
    tariff_id = _tariff_id(tariff.get("id"))
    if tariff_id is None:
        return []

    group_code = str(tariff.get("group_code") or "").lower()
    special = "trial" if is_trial else group_code if group_code in _SPECIAL_GROUP_CODES else None
    scope = cluster_servers if binding_scope is None else binding_scope
    has_direct_binding = any(_has_tariff_binding(server, tariff_id) for server in scope)
    if has_direct_binding:
        allowed = [server for server in cluster_servers if _has_tariff_binding(server, tariff_id)]
    else:
        subgroup = tariff.get("subgroup_title")
        has_subgroup_binding = (
            isinstance(subgroup, str)
            and bool(subgroup)
            and any(_has_legacy_binding(server, subgroup, tariff.get("group_code")) for server in scope)
        )
        if has_subgroup_binding:
            allowed = [
                server for server in cluster_servers if _has_legacy_binding(server, subgroup, tariff.get("group_code"))
            ]
        elif special and (subgroup is None or subgroup == ""):
            allowed = cluster_servers
        elif not special and (subgroup is None or isinstance(subgroup, str)):
            allowed = _group_only_servers(cluster_servers, tariff, scope)
        else:
            return []

    allowed = [server for server in allowed if server.get("enabled") is True]
    if special:
        allowed = [
            server
            for server in allowed
            if isinstance(server.get("special_groups"), list | tuple | set | frozenset)
            and special in server["special_groups"]
        ]
    if selected_server is not None:
        allowed = [server for server in allowed if server.get("server_name") == selected_server]
    return allowed
