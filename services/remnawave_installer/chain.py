import ipaddress
import time

from .types import ENTRY_ROLES, TCP_REALITY_TRANSPORTS, InstallError, InstallSpec


async def verify_cascade_chain(snapshot, spec, artifacts, panel, checkpoint, log, targets, load_target) -> None:
    """Проверяет каждое направление настоящим клиентом через вход."""
    if spec.role not in ENTRY_ROLES or spec.transport not in TCP_REALITY_TRANSPORTS:
        return
    from .cascade_probe import check_cascade_client
    from .remote import RemoteServer

    report = {"status": "running", "routes": [], "fingerprint": spec.fingerprint()}
    artifacts["chain_verification"] = report
    await checkpoint(artifacts)

    for index, route in enumerate(spec.cascade_specs()):
        checked = targets[route.exit_install_id]
        target = await load_target(route.exit_install_id, snapshot["owner_id"], include_secrets=True)
        if target["params"] != checked["params"] or target["status"] not in {"succeeded", "warning"}:
            raise InstallError("cascade_chain", "Выход изменился перед полной проверкой; повторите проверку")
        exit_spec = InstallSpec.from_dict(target["params"])
        secrets = target.get("secrets") or {}
        if not (secrets.get("ssh_password") or secrets.get("ssh_private_key")):
            raise InstallError(
                "cascade_chain",
                "Для полной проверки обновите SSH-доступ выходной ноды «" + exit_spec.name + "»",
                retryable=False,
            )
        state = artifacts if index == 0 else artifacts["cascade_routes"][route.exit_install_id]
        if route.role == "haproxy_entry":
            endpoint = state["haproxy"]
            server_name = endpoint["reality_domain"]
            public_key = endpoint["public_key"]
            short_id = endpoint["short_id"]
            inbound_uuid = endpoint["inbound_uuid"]
            check_site = exit_spec.transport == "vless_tcp_selfsteal"
        else:
            server_name = route.reality_server_name()
            public_key = state.get("public_key") or artifacts["public_key"]
            short_id = state.get("short_id") or artifacts["short_id"]
            inbound_uuid = state["inbound_uuid"]
            check_site = route.transport == "vless_tcp_selfsteal"
        result = {
            "name": route.host_remark or route.name,
            "client_port": route.client_port,
            "exit_address": exit_spec.transit_address(),
            "status": "running",
        }
        report["routes"].append(result)
        await checkpoint(artifacts)
        await log("Проверка всей цепочки: " + result["name"], stage="cascade_chain")
        try:
            async with panel._route_scope(artifacts, state) as scoped:
                async with panel.probe_access(inbound_uuid, scoped) as access:
                    async with RemoteServer(exit_spec, secrets) as remote_exit:
                        observed = await check_cascade_client(
                            remote_exit,
                            entry_address=route.address,
                            entry_port=route.client_port,
                            server_name=server_name,
                            public_key=public_key,
                            short_id=short_id,
                            probe_uuid=access["uuid"],
                            expected_exit_ip=exit_spec.exit_ip,
                            check_site=check_site,
                        )
                    try:
                        address = str(ipaddress.ip_address(observed["observed_exit_ip"]))
                    except (ValueError, KeyError, TypeError):
                        raise InstallError("cascade_chain", "Внешний IP полной цепочки не подтверждён") from None
        except BaseException:
            result["status"] = report["status"] = "failed"
            await checkpoint(artifacts)
            raise
        result.update(status="passed", observed_exit_ip=address)
        await checkpoint(artifacts)
        await log("Цепочка проверена: " + result["name"] + ", внешний IP " + address, stage="cascade_chain")
    report.update(status="passed", checked_at=time.time())
    await checkpoint(artifacts)
