import copy
import ipaddress
import json
import re
import shlex

from .psiphon import public_psiphon_config
from .remote import APT, ROOT, RemoteServer, claim_paths, transport_ports
from .types import InstallError, InstallSpec


PSIPHON_COMMIT = "c4a09096ab401b57f52e092c8f2f829c8fac7628"
SURICATA_VERSION = "8.0.7"
SURICATA_SHA256 = "a9d46a9c8f40e81f8a6df3ecc2c5a9a8e8da985876299108d92da547c013ba8c"
NDPI_COMMIT = "90090b9ae841fce67d00c52ac87e755dcea7c95f"
_ORDER = ("bbr", "fail2ban", "warp", "psiphon", "antitorrent", "ufw")
_SHAPING_DEFAULTS = {
    "enabled": True,
    "ceiling_mbps": 200,
    "penalty_mbps": 100,
    "threshold_seconds": 30,
    "penalty_seconds": 300,
    "volume_gb": 0,
    "volume_window_hours": 1,
}


def shaping_config(value: dict) -> dict:
    """Проверяет параметры ограничения скорости."""
    if not isinstance(value, dict) or set(value) - set(_SHAPING_DEFAULTS):
        raise InstallError("shaping", "Неизвестные параметры шейпинга", retryable=False)
    config = {**_SHAPING_DEFAULTS, **value}
    if type(config["enabled"]) is not bool:
        raise InstallError("shaping", "Параметр enabled должен быть логическим", retryable=False)
    bounds = {
        "ceiling_mbps": (1, 100000),
        "penalty_mbps": (1, 100000),
        "threshold_seconds": (5, 86400),
        "penalty_seconds": (10, 604800),
        "volume_gb": (0, 100000),
        "volume_window_hours": (1, 720),
    }
    for name, (minimum, maximum) in bounds.items():
        if type(config[name]) is not int or not minimum <= config[name] <= maximum:
            raise InstallError("shaping", f"Некорректное значение {name}", retryable=False)
    if config["penalty_mbps"] > config["ceiling_mbps"]:
        raise InstallError("shaping", "Штрафная скорость превышает основной потолок", retryable=False)
    return config


def psiphon_config(secrets: dict) -> tuple[dict, list[str]]:
    """Выбирает публичный профиль или проверяет пользовательский JSON."""
    value = secrets.get("psiphon_config")
    if value is None or value == "":
        value = public_psiphon_config()
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = None
    if not isinstance(value, dict) or not value.get("PropagationChannelId") or not value.get("SponsorId"):
        raise InstallError(
            "preflight", "Для Psiphon нужен JSON конфигурации сети с SponsorId и PropagationChannelId", retryable=False
        )
    if not value.get("TargetServerEntry") and not (
        value.get("RemoteServerListURLs") and value.get("RemoteServerListSignaturePublicKey")
    ):
        raise InstallError(
            "preflight", "Конфигурация Psiphon не содержит сервер или подписанный список серверов", retryable=False
        )
    config = copy.deepcopy(value)
    regions = config.pop("EgressRegions", ["US", "NL", "DE"])
    if (
        not isinstance(regions, list)
        or not 1 <= len(regions) <= 3
        or any(not isinstance(item, str) or not re.fullmatch(r"[A-Z]{2}", item) for item in regions)
    ):
        raise InstallError("preflight", "Укажите от одной до трёх стран Psiphon в EgressRegions", retryable=False)
    config.update(
        DataRootDirectory=ROOT + "/addons/psiphon-data",
        LocalSocksProxyPort=40080,
        DisableLocalHTTPProxy=True,
        ListenInterface="",
        EmitDiagnosticNotices=False,
        EmitDiagnosticNetworkParameters=False,
        TunnelPoolSize=1,
    )
    return config, regions


def validate_addons(spec: InstallSpec, secrets: dict) -> None:
    """Проверяет дополнения до удалённых изменений."""
    if "psiphon" in spec.addons:
        psiphon_config(secrets)
    if spec.shaping:
        shaping_config(spec.shaping)


async def _packages(remote: RemoteServer, packages: str) -> None:
    """Ставит пакеты без удаления существующих зависимостей."""
    await remote.run(f"{APT} update && {APT} install -y {packages}", step="addons", timeout=900)


async def _bbr(remote: RemoteServer, spec: InstallSpec, artifacts: dict, checkpoint) -> None:
    """Включает BBR с сохранением исходных значений."""
    available = await remote.run(
        "modprobe tcp_bbr 2>/dev/null || true; sysctl -n net.ipv4.tcp_available_congestion_control", step="bbr"
    )
    if "bbr" not in available.split():
        raise InstallError("bbr", "Текущее ядро не поддерживает BBR", retryable=False)
    if "bbr_previous" not in artifacts:
        artifacts["bbr_previous"] = {
            key: (await remote.run(f"sysctl -n {key}", step="bbr")).strip()
            for key in ("net.core.default_qdisc", "net.ipv4.tcp_congestion_control")
        }
        if checkpoint:
            await checkpoint(artifacts)
    path = "/etc/sysctl.d/90-solobot-remnawave-bbr.conf"
    await remote.write(path, "net.core.default_qdisc=fq\nnet.ipv4.tcp_congestion_control=bbr\n", mode=0o644)
    await remote.run(f'sysctl -p {path} && test "$(sysctl -n net.ipv4.tcp_congestion_control)" = bbr', step="bbr")


async def _fail2ban(remote: RemoteServer, spec: InstallSpec, artifacts: dict, checkpoint) -> None:
    """Защищает SSH и исключает адрес управляющего соединения."""
    if "fail2ban_was_active" not in artifacts:
        status = await remote.run("systemctl is-active fail2ban 2>/dev/null || true", step="fail2ban")
        artifacts["fail2ban_was_active"] = status.strip() == "active"
        if checkpoint:
            await checkpoint(artifacts)
    await _packages(remote, "fail2ban")
    connection = await remote.run("printf '%s' \"$SSH_CONNECTION\"", step="fail2ban")
    sources = ["127.0.0.0/8", "::1", *spec.trusted_ssh_sources]
    if connection.split():
        try:
            sources.append(str(ipaddress.ip_address(connection.split()[0])))
        except ValueError:
            pass
    await remote.write(
        "/etc/fail2ban/jail.d/solobot-remnawave.conf",
        (
            f"[sshd]\nenabled=true\nport={spec.ssh_port}\nbackend=systemd\n"
            "maxretry=5\nbantime=3600\nfindtime=600\nignoreip=" + " ".join(sources) + "\n"
        ),
        mode=0o644,
    )
    await remote.run(
        "fail2ban-client -t && systemctl enable --now fail2ban && systemctl restart fail2ban", step="fail2ban"
    )


async def _warp(remote: RemoteServer, spec: InstallSpec, artifacts: dict, checkpoint) -> None:
    """Запускает официальный WARP только как локальный SOCKS-прокси."""
    existing = await remote.run("command -v warp-cli >/dev/null && echo YES || true", step="warp")
    if "YES" in existing and not artifacts.get("warp_owned") and not spec.replace_existing:
        raise InstallError("warp", "WARP уже установлен. Подтвердите изменение его режима", retryable=False)
    artifacts["warp_owned"] = True
    if checkpoint:
        await checkpoint(artifacts)
    await _packages(remote, "curl ca-certificates gnupg")
    key = await remote.run("curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg", step="warp")
    if "BEGIN PGP PUBLIC KEY BLOCK" not in key:
        raise InstallError("warp", "Не получен ключ официального репозитория WARP")
    distribution = (await remote.run(". /etc/os-release; printf '%s' \"$VERSION_CODENAME\"", step="warp")).strip()
    if distribution not in {"jammy", "noble", "resolute"}:
        raise InstallError("warp", "Версия Ubuntu не поддерживается официальным WARP", retryable=False)
    await remote.write("/etc/apt/keyrings/solobot-warp.asc", key, mode=0o644)
    await remote.write(
        "/etc/apt/sources.list.d/solobot-warp.list",
        (
            "deb [signed-by=/etc/apt/keyrings/solobot-warp.asc] https://pkg.cloudflareclient.com/ "
            + distribution
            + " main\n"
        ),
        mode=0o644,
    )
    await remote.run(
        f"{APT} update && {APT} install -y cloudflare-warp && systemctl enable --now warp-svc",
        step="warp",
        timeout=900,
    )
    await remote.run(
        "warp-cli --accept-tos registration show >/dev/null 2>&1 || warp-cli --accept-tos registration new",
        step="warp",
        timeout=120,
        secret=True,
    )
    await remote.run(
        "warp-cli --accept-tos mode proxy && warp-cli --accept-tos proxy port 40000 && warp-cli --accept-tos connect",
        step="warp",
        timeout=90,
    )
    await remote.run(
        "for i in $(seq 1 30); do "
        "curl -fsS --max-time 10 --proxy socks5h://127.0.0.1:40000 https://www.cloudflare.com/cdn-cgi/trace "
        "| grep -Eq '^warp=(on|plus)$' && exit 0; sleep 3; done; exit 1",
        step="warp",
        timeout=420,
    )
    version = await remote.run("dpkg-query -W -f='${Version}' cloudflare-warp", step="warp")
    artifacts["warp_version"] = version.strip()


async def _psiphon(remote: RemoteServer, spec: InstallSpec, artifacts: dict, checkpoint) -> None:
    """Собирает закреплённый Psiphon и проверяет три региона."""
    config, regions = psiphon_config(remote.secrets)
    await _packages(remote, "git ca-certificates curl")
    source = ROOT + "/addons/psiphon-src"
    binary = ROOT + "/addons/bin"
    await remote.run(
        f"mkdir -p {source} {binary} {ROOT}/addons/psiphon-data && cd {source} && test -d .git || git init",
        step="psiphon",
    )
    await remote.run(
        f"cd {source} && git remote remove origin 2>/dev/null; "
        f"cd {source} && git remote add origin https://github.com/Psiphon-Labs/psiphon-tunnel-core.git && "
        f"git fetch --depth 1 origin {PSIPHON_COMMIT} && git checkout --detach {PSIPHON_COMMIT} && "
        f'test "$(git rev-parse HEAD)" = {PSIPHON_COMMIT}',
        step="psiphon",
        timeout=600,
    )
    await remote.run(
        f"docker run --rm -v {source}:/src -v {binary}:/out -w /src golang:1.26.0-bookworm "
        "bash -c 'go build -mod=readonly -trimpath -o /out/psiphon ./ConsoleClient' && "
        f"chmod 0700 {binary}/psiphon",
        step="psiphon",
        timeout=1800,
    )
    await remote.write(
        "/etc/systemd/system/solobot-psiphon.service",
        (
            "[Unit]\nDescription=Node local Psiphon proxy\nAfter=network-online.target\nWants=network-online.target\n"
            f"[Service]\nType=simple\nWorkingDirectory={ROOT}/addons/psiphon-data\n"
            f"ExecStart={binary}/psiphon -config {ROOT}/addons/psiphon.json\n"
            "Restart=on-failure\nRestartSec=5\nNoNewPrivileges=true\nPrivateTmp=true\nUMask=0077\n"
            "[Install]\nWantedBy=multi-user.target\n"
        ),
        mode=0o644,
    )
    await remote.run("systemctl daemon-reload && systemctl enable solobot-psiphon", step="psiphon")
    for region in regions:
        config["EgressRegion"] = region
        await remote.write(ROOT + "/addons/psiphon.json", json.dumps(config))
        await remote.run("systemctl restart solobot-psiphon", step="psiphon")
        try:
            await remote.run(
                "for i in $(seq 1 30); do "
                "curl -fsS --max-time 5 --proxy socks5h://127.0.0.1:40080 "
                "https://www.gstatic.com/generate_204 >/dev/null && exit 0; sleep 2; done; exit 1",
                step="psiphon",
                timeout=240,
            )
            artifacts["psiphon_region"] = region
            artifacts["psiphon_commit"] = PSIPHON_COMMIT
            if checkpoint:
                await checkpoint(artifacts)
            return
        except InstallError:
            continue
    raise InstallError("psiphon", "Ни один из выбранных регионов Psiphon не подтвердил рабочий туннель")


_TORRENT_RULES = """drop ip any any -> any any (msg:"Node nDPI BitTorrent"; ndpi-protocol:BitTorrent; sid:9900100; rev:1;)
drop tcp any any -> any any (msg:"Node BitTorrent handshake"; flow:established; content:"|13|BitTorrent protocol"; depth:20; sid:9900101; rev:1;)
drop udp any any -> any any (msg:"Node BitTorrent tracker connect"; content:"|00 00 04 17 27 10 19 80 00 00 00 00|"; depth:12; sid:9900102; rev:1;)
drop udp any any -> any any (msg:"Node BitTorrent DHT query"; content:"d1:"; depth:3; content:"1:y1:q"; sid:9900103; rev:1;)
"""


async def _antitorrent(remote: RemoteServer, spec: InstallSpec, artifacts: dict, checkpoint) -> None:
    """Собирает Suricata с nDPI и проверяет обработчик NFQUEUE."""
    await _packages(
        remote,
        "nftables build-essential autoconf automake libtool pkg-config git curl ca-certificates "
        "libpcap-dev libpcre2-dev libyaml-dev zlib1g-dev libjansson-dev libcap-ng-dev "
        "libnetfilter-queue-dev libnetfilter-log-dev libnet-dev libmagic-dev liblz4-dev libssl-dev",
    )
    prefix = ROOT + "/addons/antitorrent"
    binary = prefix + "/bin/suricata"
    configuration = prefix + "/etc/suricata/suricata.yaml"
    plugin = prefix + "/lib/suricata/ndpi.so"
    cached = await remote.run(
        f"test -x {binary} && test -s {plugin} && test -s {configuration} && {binary} -V && echo BUILD_OK || true",
        step="antitorrent",
    )
    if SURICATA_VERSION not in cached or "BUILD_OK" not in cached:
        build = ROOT + "/addons/antitorrent-build"
        await remote.run(f"mkdir -p {build}/suricata {build}/ndpi {prefix}", step="antitorrent")
        await remote.run(f"cd {build}/ndpi && test -d .git || git init", step="antitorrent")
        await remote.run(
            f"cd {build}/ndpi && git remote remove origin 2>/dev/null; "
            f"cd {build}/ndpi && git remote add origin https://github.com/ntop/nDPI.git && "
            f"git fetch --depth 1 origin {NDPI_COMMIT} && git checkout --detach {NDPI_COMMIT} && "
            f'test "$(git rev-parse HEAD)" = {NDPI_COMMIT} && '
            "CFLAGS='-O2 -fPIC' ./autogen.sh && make -j2",
            step="antitorrent",
            timeout=1800,
        )
        await remote.run(
            f"cd {build} && curl -fsSL https://www.openinfosecfoundation.org/download/suricata-{SURICATA_VERSION}.tar.gz "
            "-o suricata.tar.gz && "
            f"printf '%s  suricata.tar.gz\\n' {SURICATA_SHA256} | sha256sum -c - && "
            "tar -xzf suricata.tar.gz -C suricata --strip-components=1",
            step="antitorrent",
            timeout=300,
        )
        rust_root = build + "/rust"
        await remote.run(
            f'mkdir -p {rust_root} && case "$(uname -m)" in '
            "x86_64) triple=x86_64-unknown-linux-gnu; hash=20a06e644b0d9bd2fbdbfd52d42540bdde820ea7df86e92e533c073da0cdd43c ;; "
            "aarch64) triple=aarch64-unknown-linux-gnu; hash=e3853c5a252fca15252d07cb23a1bdd9377a8c6f3efa01531109281ae47f841c ;; "
            "*) exit 1 ;; esac && "
            f"cd {rust_root} && curl -fsSL https://static.rust-lang.org/rustup/archive/1.28.2/$triple/rustup-init "
            "-o rustup-init && printf '%s  rustup-init\\n' \"$hash\" | sha256sum -c - && chmod 0700 rustup-init && "
            f"RUSTUP_HOME={rust_root}/rustup CARGO_HOME={rust_root}/cargo "
            "./rustup-init -y --no-modify-path --profile minimal --default-toolchain 1.85.1",
            step="antitorrent",
            timeout=900,
        )
        await remote.run(
            f"export RUSTUP_HOME={rust_root}/rustup CARGO_HOME={rust_root}/cargo "
            f"PATH={rust_root}/cargo/bin:$PATH; cd {build}/suricata && "
            f"./configure --prefix={prefix} --sysconfdir={prefix}/etc --localstatedir={prefix}/var "
            f"--enable-nfqueue --enable-ndpi --with-ndpi={build}/ndpi --disable-gccmarch-native && "
            "make -j2 && make install && make install-conf",
            step="antitorrent",
            timeout=1800,
        )
    path = ROOT + "/addons/antitorrent.rules"
    await remote.write(path, _TORRENT_RULES, mode=0o600)
    await remote.run(
        f"mkdir -p {ROOT}/addons/suricata-log && {binary} --build-info | grep -Eq 'NFQueue.*yes' && "
        f"test -s {plugin} && grep -Fq {shlex.quote(plugin)} {configuration}",
        step="antitorrent",
    )
    await remote.run(
        f"{binary} -T -c {configuration} -S {path} -l {ROOT}/addons/suricata-log",
        step="antitorrent",
        timeout=120,
    )
    await remote.write(
        "/etc/systemd/system/solobot-antitorrent.service",
        (
            "[Unit]\nDescription=Node BitTorrent packet filter\nAfter=network-online.target\n"
            f"[Service]\nExecStart={binary} -c {configuration} -S {path} "
            f"-q 2093 --set stream.inline=yes -l {ROOT}/addons/suricata-log --pidfile {ROOT}/addons/suricata.pid\n"
            "Restart=on-failure\nRestartSec=5\n[Install]\nWantedBy=multi-user.target\n"
        ),
        mode=0o644,
    )
    await remote.run(
        "systemctl daemon-reload && systemctl enable --now solobot-antitorrent && systemctl is-active --quiet solobot-antitorrent",
        step="antitorrent",
    )
    await remote.run(
        "for i in $(seq 1 15); do awk '$1 == 2093 { found=1 } END { exit !found }' "
        "/proc/net/netfilter/nfnetlink_queue 2>/dev/null && exit 0; sleep 1; done; exit 1",
        step="antitorrent",
        timeout=30,
    )
    await _nft_service(remote, "antitorrent", _antitorrent_rules(spec))
    artifacts["suricata_version"] = SURICATA_VERSION
    artifacts["ndpi_version"] = "4.14"
    artifacts["ndpi_commit"] = NDPI_COMMIT
    if checkpoint:
        await checkpoint(artifacts)


def _antitorrent_rules(spec: InstallSpec) -> str:
    """Исключает управляющие соединения из очереди фильтра."""
    protected = ", ".join(map(str, sorted({spec.ssh_port, spec.node_port, *transport_ports(spec)})))
    return (
        "table inet solobot_antitorrent {\n"
        "chain inbound { type filter hook input priority -10; policy accept;\n"
        f"iifname != lo tcp dport != {{ {protected} }} queue num 2093 bypass\n"
        "iifname != lo meta l4proto udp queue num 2093 bypass\n}\n"
        "chain outbound { type filter hook output priority -10; policy accept;\n"
        f"oifname != lo tcp sport != {{ {protected} }} queue num 2093 bypass\n"
        "oifname != lo meta l4proto udp queue num 2093 bypass\n}\n}\n"
    )


async def _nft_service(remote: RemoteServer, name: str, rules: str) -> None:
    """Загружает только таблицу правил установщика."""
    path = ROOT + f"/addons/{name}.nft"
    table = "solobot_" + name
    await remote.write(path, rules, mode=0o600)
    apply_path = ROOT + f"/addons/apply-{name}.py"
    apply_script = (
        "import pathlib,subprocess\n"
        f"table={table!r}\nrules=pathlib.Path({path!r}).read_text()\n"
        "present=subprocess.run(['nft','list','table','inet',table],capture_output=True).returncode==0\n"
        "batch=('delete table inet '+table+'\\n' if present else '')+rules\n"
        "subprocess.run(['nft','-c','-f','-'],input=batch,text=True,check=True)\n"
        "subprocess.run(['nft','-f','-'],input=batch,text=True,check=True)\n"
    )
    await remote.write(apply_path, apply_script)
    await remote.run(
        f"python3 {apply_path}",
        step=name,
    )
    await remote.write(
        f"/etc/systemd/system/solobot-{name}-nft.service",
        (
            f"[Unit]\nDescription=Node {name} firewall rules\nAfter=network-pre.target\nBefore=network.target\n"
            f"[Service]\nType=oneshot\nRemainAfterExit=yes\nExecStart=/usr/bin/python3 {apply_path}\n"
            f"ExecStop=/usr/sbin/nft delete table inet {table}\n[Install]\nWantedBy=multi-user.target\n"
        ),
        mode=0o644,
    )
    await remote.run(f"systemctl daemon-reload && systemctl enable solobot-{name}-nft", step=name)


async def _ufw(remote: RemoteServer, spec: InstallSpec, artifacts: dict, checkpoint) -> None:
    """Включает UFW, сохраняя SSH и порты установщика."""
    if "ufw_was_active" not in artifacts:
        previous = await remote.run("if command -v ufw >/dev/null; then LC_ALL=C ufw status; fi", step="ufw")
        artifacts["ufw_was_active"] = "Status: active" in previous
        previous_added = await remote.run("if command -v ufw >/dev/null; then LC_ALL=C ufw show added; fi", step="ufw")
        artifacts["ufw_previous_added"] = [tokens for tokens, _owned in _ufw_commands(previous_added)]
        if checkpoint:
            await checkpoint(artifacts)
    await _packages(remote, "ufw")
    if spec.trusted_ssh_sources:
        for source in spec.trusted_ssh_sources:
            await remote.run(
                f"ufw allow from {shlex.quote(source)} to any port {spec.ssh_port} proto tcp comment solobot-remnawave",
                step="ufw",
            )
    else:
        await remote.run(f"ufw allow {spec.ssh_port}/tcp comment solobot-remnawave", step="ufw")
    if not spec.panel_sources:
        await remote.run(f"ufw allow {spec.node_port}/tcp comment solobot-remnawave", step="ufw")
    for source in spec.panel_sources:
        await remote.run(
            f"ufw allow from {shlex.quote(source)} to any port {spec.node_port} proto tcp comment solobot-remnawave",
            step="ufw",
        )
    for port in transport_ports(spec):
        await remote.run(f"ufw allow {port}/tcp comment solobot-remnawave", step="ufw")
    listeners = await remote.run("ss -tlnpH", step="ufw")
    for line in listeners.splitlines():
        if not any(
            f'"{name}"' in line for name in ("xray", "rw-core", "rw-node", "nginx", "openresty", "caddy", "sshd")
        ):
            continue
        fields = line.split()
        if len(fields) < 4 or fields[3].startswith(("127.", "[::1]:")):
            continue
        port = fields[3].rsplit(":", 1)[-1]
        if port.isdecimal() and 1 <= int(port) <= 65535 and int(port) not in {spec.ssh_port, spec.node_port}:
            await remote.run(f"ufw allow {int(port)}/tcp comment solobot-remnawave", step="ufw")
    await remote.run("ufw --force enable && LC_ALL=C ufw status | grep -q 'Status: active'", step="ufw")


def _ufw_commands(output: str) -> list[tuple[list[str], bool]]:
    """Разбирает правила UFW без изменения существующих комментариев."""
    result = []
    for line in output.splitlines():
        try:
            tokens = shlex.split(line)
        except ValueError:
            continue
        if len(tokens) < 3 or tokens[0] != "ufw" or tokens[1] not in {"allow", "deny", "reject", "limit", "route"}:
            continue
        owned = False
        if "comment" in tokens:
            index = tokens.index("comment")
            owned = index + 1 < len(tokens) and tokens[index + 1] == "solobot-remnawave"
            tokens = tokens[:index]
        result.append((tokens[1:], owned))
    return result


async def _remove_ufw(remote: RemoteServer, artifacts: dict) -> None:
    """Удаляет добавленные правила, сохраняя прежние и посторонние."""
    if "ufw_previous_added" not in artifacts:
        raise InstallError("ufw", "Исходные правила UFW не сохранены", retryable=False)
    previous = {tuple(tokens) for tokens in artifacts["ufw_previous_added"]}
    current = await remote.run("LC_ALL=C ufw show added", step="ufw")
    for tokens, owned in _ufw_commands(current):
        if owned and tuple(tokens) not in previous:
            await remote.run("ufw --force delete " + shlex.join(tokens), step="ufw")
    if artifacts.get("ufw_was_active") is False:
        await remote.run("ufw --force disable", step="ufw")


def _shaper_rules(spec: InstallSpec, config: dict) -> str:
    """Создаёт отдельные ограничения скорости для IPv4 и IPv6."""
    ceiling = config["ceiling_mbps"] * 125000
    penalty = config["penalty_mbps"] * 125000
    chunks = [
        "table inet solobot_shaper {",
        "set penalty4 { type ipv4_addr; flags timeout; }",
        "set penalty6 { type ipv6_addr; flags timeout; }",
    ]
    for direction, hook, address, port in [
        ("inbound", "input", "saddr", "dport"),
        ("outbound", "output", "daddr", "sport"),
    ]:
        chunks.append(f"chain {direction} {{ type filter hook {hook} priority -5; policy accept;")
        for family, number in [("ip", 4), ("ip6", 6)]:
            key = f"{family} {address}"
            match = f"tcp {port} {spec.client_port}"
            chunks.append(
                f"{match} {key} @penalty{number} meter p{number}{direction} "
                f"{{ {key} timeout 1m limit rate over {penalty} bytes/second burst 1048576 bytes }} drop"
            )
            chunks.append(
                f"{match} meter c{number}{direction} "
                f"{{ {key} timeout 1m limit rate over {ceiling} bytes/second burst 1048576 bytes }} drop"
            )
        chunks.append("}")
    return "\n".join([*chunks, "}", ""])


_SHAPER_DAEMON = r"""import collections
import ipaddress
import json
import pathlib
import re
import subprocess
import time

root=pathlib.Path("/opt/solobot-remnawave/addons")
config=json.loads((root/"shaping.json").read_text())
port=int(config["client_port"])
previous={}
saturated={}
volume={}
penalties={}
last=time.monotonic()
while True:
 now=time.monotonic(); elapsed=max(now-last,1); last=now
 result=subprocess.run(["conntrack","-L","-p","tcp","--dport",str(port),"-o","extended"],capture_output=True,text=True)
 current={}
 rates=collections.defaultdict(int)
 if result.returncode not in (0,1):
  time.sleep(5); continue
 for line in result.stdout.splitlines():
  match=re.search(r"src=(\S+) dst=(\S+) sport=(\d+) dport=(\d+).*?bytes=(\d+).*?bytes=(\d+)",line)
  if not match: continue
  address,dest,sport,dport,upload,download=match.groups()
  try: ipaddress.ip_address(address)
  except ValueError: continue
  key=(address,dest,sport,dport)
  total=int(upload)+int(download)
  current[key]=total
  delta=max(0,total-previous.get(key,total))
  rates[address]+=delta/elapsed
  volume.setdefault(address,collections.deque()).append((now,delta))
 previous=current
 for address,samples in list(volume.items()):
  while samples and samples[0][0]<now-int(config["volume_window_hours"])*3600: samples.popleft()
  if rates[address]>=int(config["ceiling_mbps"])*125000*0.90:
   saturated.setdefault(address,now)
  else: saturated.pop(address,None)
  excess=(address in saturated and now-saturated[address]>=int(config["threshold_seconds"]))
  excess=excess or (int(config["volume_gb"])>0 and sum(value for stamp,value in samples)>=int(config["volume_gb"])*1024**3)
  if excess and penalties.get(address,0)<=now:
   seconds=int(config["penalty_seconds"])
   family=ipaddress.ip_address(address).version
   command="add element inet solobot_shaper penalty"+str(family)+" { "+address+" timeout "+str(seconds)+"s }\n"
   added=subprocess.run(["nft","-f","-"],input=command,text=True,capture_output=True)
   if added.returncode==0:
    penalties[address]=now+seconds
    saturated.pop(address,None)
  if not samples and penalties.get(address,0)<=now:
   volume.pop(address,None); saturated.pop(address,None); penalties.pop(address,None)
 time.sleep(5)
"""


async def _shaping(remote: RemoteServer, spec: InstallSpec, artifacts: dict, value: dict) -> None:
    """Устанавливает проверяемый шейпер с индивидуальными штрафами."""
    config = shaping_config(value)
    if not config["enabled"]:
        await _disable_shaping(remote, artifacts)
        return
    if spec.transport.startswith("cdn_") or spec.role == "exit":
        raise InstallError("shaping", "Для CDN и выхода каскада шейпинг недоступен", retryable=False)
    await _packages(remote, "nftables conntrack python3")
    if "conntrack_accounting_previous" not in artifacts:
        artifacts["conntrack_accounting_previous"] = (
            await remote.run("sysctl -n net.netfilter.nf_conntrack_acct", step="shaping")
        ).strip()
    await remote.write("/etc/sysctl.d/90-solobot-conntrack.conf", "net.netfilter.nf_conntrack_acct=1\n", mode=0o644)
    await remote.run("sysctl -p /etc/sysctl.d/90-solobot-conntrack.conf", step="shaping")
    await remote.write(ROOT + "/addons/shaping.json", json.dumps({**config, "client_port": spec.client_port}))
    await remote.write(ROOT + "/addons/traffic-shaper.py", _SHAPER_DAEMON)
    await _nft_service(remote, "shaper", _shaper_rules(spec, config))
    await remote.write(
        "/etc/systemd/system/solobot-shaper.service",
        (
            "[Unit]\nDescription=Node per-client traffic shaper\nAfter=network-online.target solobot-shaper-nft.service\n"
            "Requires=solobot-shaper-nft.service\n"
            f"[Service]\nExecStart=/usr/bin/python3 {ROOT}/addons/traffic-shaper.py\n"
            "Restart=on-failure\nRestartSec=5\n[Install]\nWantedBy=multi-user.target\n"
        ),
        mode=0o644,
    )
    await remote.run(
        "systemctl daemon-reload && systemctl enable --now solobot-shaper && systemctl restart solobot-shaper && systemctl is-active --quiet solobot-shaper",
        step="shaping",
    )
    artifacts["shaping"] = config


async def _disable_shaping(remote: RemoteServer, artifacts: dict) -> None:
    """Удаляет только собственные правила шейпинга."""
    await remote.run(
        "systemctl disable --now solobot-shaper solobot-shaper-nft 2>/dev/null || true; "
        "nft list table inet solobot_shaper >/dev/null 2>&1 && nft delete table inet solobot_shaper; "
        "rm -f /etc/systemd/system/solobot-shaper.service /etc/systemd/system/solobot-shaper-nft.service "
        "/etc/sysctl.d/90-solobot-conntrack.conf; systemctl daemon-reload",
        step="shaping",
    )
    previous = artifacts.get("conntrack_accounting_previous")
    if previous in {"0", "1"}:
        await remote.run(f"sysctl -w net.netfilter.nf_conntrack_acct={previous}", step="shaping")
    artifacts["shaping"] = {"enabled": False}


async def install_addons(remote: RemoteServer, spec: InstallSpec, artifacts: dict, log, checkpoint=None) -> None:
    """Устанавливает дополнения и сохраняет подтверждённые результаты."""
    validate_addons(spec, remote.secrets)
    await claim_paths(remote, spec)
    installed = list(artifacts.get("installed_addons") or [])
    functions = {
        "bbr": _bbr,
        "fail2ban": _fail2ban,
        "warp": _warp,
        "psiphon": _psiphon,
        "antitorrent": _antitorrent,
        "ufw": _ufw,
    }
    for name in _ORDER:
        if name not in spec.addons:
            continue
        await log("Установка дополнения: " + name)
        await functions[name](remote, spec, artifacts, checkpoint)
        if name not in installed:
            installed.append(name)
        artifacts["installed_addons"] = installed
        if checkpoint:
            await checkpoint(artifacts)
    if spec.shaping:
        await log("Настройка ограничений скорости")
        await _shaping(remote, spec, artifacts, spec.shaping)
        if checkpoint:
            await checkpoint(artifacts)


async def manage_addons(
    remote: RemoteServer, spec: InstallSpec, artifacts: dict, action: str, params: dict, log, checkpoint=None
) -> None:
    """Изменяет только дополнения собственной установки."""
    await claim_paths(remote, spec)
    if action in {"shaping", "shaper"}:
        value = params.get("config", params)
        await _shaping(remote, spec, artifacts, value)
    elif action in {"addon", "addon_uninstall"}:
        name = params.get("name")
        if name not in _ORDER:
            raise InstallError("addons", "Неизвестное дополнение", retryable=False)
        if params.get("enabled") is True:
            spec = copy.deepcopy(spec)
            spec.addons = [name]
            spec.shaping = {}
            await install_addons(remote, spec, artifacts, log, checkpoint)
            return
        if name not in (artifacts.get("installed_addons") or []):
            await log("Дополнение уже отсутствует: " + name)
            return
        await log("Удаление дополнения: " + name)
        if name == "bbr":
            previous = artifacts.get("bbr_previous") or {}
            if set(previous) != {"net.core.default_qdisc", "net.ipv4.tcp_congestion_control"}:
                raise InstallError("bbr", "Исходные настройки BBR не сохранены", retryable=False)
            for key, value in previous.items():
                if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", value):
                    raise InstallError("bbr", "Некорректный снимок исходных настроек", retryable=False)
                await remote.run(f"sysctl -w {key}={value}", step="bbr")
            await remote.run("rm -f /etc/sysctl.d/90-solobot-remnawave-bbr.conf", step="bbr")
        elif name == "fail2ban":
            await remote.run(
                "rm -f /etc/fail2ban/jail.d/solobot-remnawave.conf; systemctl restart fail2ban", step="fail2ban"
            )
            if artifacts.get("fail2ban_was_active") is False:
                await remote.run("systemctl disable --now fail2ban", step="fail2ban")
        elif name == "warp":
            if not artifacts.get("warp_owned"):
                raise InstallError("warp", "Принадлежность WARP не подтверждена", retryable=False)
            await remote.run("warp-cli --accept-tos disconnect && systemctl disable --now warp-svc", step="warp")
        elif name == "psiphon":
            await remote.run(
                "systemctl disable --now solobot-psiphon; rm -f /etc/systemd/system/solobot-psiphon.service; systemctl daemon-reload",
                step="psiphon",
            )
        elif name == "antitorrent":
            await remote.run(
                "systemctl disable --now solobot-antitorrent solobot-antitorrent-nft 2>/dev/null || true; "
                "nft list table inet solobot_antitorrent >/dev/null 2>&1 && nft delete table inet solobot_antitorrent; "
                "rm -f /etc/systemd/system/solobot-antitorrent.service /etc/systemd/system/solobot-antitorrent-nft.service; "
                "systemctl daemon-reload",
                step="antitorrent",
            )
        elif name == "ufw":
            await _remove_ufw(remote, artifacts)
        artifacts["installed_addons"] = [item for item in artifacts.get("installed_addons", []) if item != name]
    else:
        raise InstallError("addons", "Неизвестное действие", retryable=False)
    if checkpoint:
        await checkpoint(artifacts)
