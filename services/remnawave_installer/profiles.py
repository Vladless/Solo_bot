import base64
import copy
import ipaddress
import secrets

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

from .types import InstallError, InstallSpec


_WARP_DOMAINS = [
    "domain:ai.google.dev",
    "domain:aistudio.google.com",
    "domain:alkalimakersuite-pa.clients6.google.com",
    "domain:anthropic.com",
    "domain:chat.com",
    "domain:chatgpt.com",
    "domain:claude.ai",
    "domain:claude.com",
    "domain:copilot.microsoft.com",
    "domain:elevenlabs.io",
    "domain:gemini.google.com",
    "domain:generativelanguage.googleapis.com",
    "domain:githubcopilot.com",
    "domain:grok.com",
    "domain:oaistatic.com",
    "domain:oaiusercontent.com",
    "domain:openai.com",
    "domain:perplexity.ai",
    "domain:pplx.ai",
    "domain:sora.com",
    "domain:x.ai",
    "geosite:google-deepmind",
]
_PSIPHON_DOMAINS = [
    "domain:gemini.google.com",
    "domain:generativelanguage.googleapis.com",
    "domain:ai.google.dev",
    "domain:aistudio.google.com",
    "domain:alkalimakersuite-pa.clients6.google.com",
    "geosite:google-deepmind",
]


def _encoded(value: bytes) -> str:
    """Кодирует ключ Xray без заполнения."""
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def generate_keys(spec: InstallSpec) -> dict:
    """Генерирует ключи транспорта и случайный путь."""
    if spec.transport.startswith("cdn_"):
        path = (
            "/"
            + "/".join([
                secrets.choice(["assets", "media", "static", "cdn", "content", "stream"]),
                secrets.choice(["v1", "v2", "v3", "api", "data"]),
                secrets.choice(["chunks", "segments", "blocks", "parts", "frames"]),
            ])
            + "/"
        )
        return {"xhttp_path": path, "xhttp_extra": _cdn_extra(spec.transport, path)}
    if spec.transport == "vless_encryption":
        return {}
    private = X25519PrivateKey.generate()
    return {
        "private_key": _encoded(private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())),
        "public_key": _encoded(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)),
        "short_id": secrets.token_hex(8),
    }


def _cdn_extra(transport: str, path: str) -> dict:
    """Возвращает параметры XHTTP выбранного CDN."""
    if transport == "cdn_vk":
        return {
            "mode": "packet-up",
            "path": path,
            "xmux": {
                "maxConnections": "2",
                "cMaxReuseTimes": "0",
                "hKeepAlivePeriod": 30,
                "hMaxRequestTimes": "100-200",
                "hMaxReusableSecs": "900-1800",
            },
            "noSSEHeader": True,
            "xPaddingKey": "_token",
            "xPaddingBytes": "16-64",
            "xPaddingHeader": "X-Signature",
            "xPaddingMethod": "tokenish",
            "uplinkHTTPMethod": "GET",
            "xPaddingObfsMode": True,
            "xPaddingPlacement": "query",
            "scMaxEachPostBytes": "500000-1000000",
            "uplinkDataPlacement": "body",
            "scMinPostsIntervalMs": "50-150",
            "scStreamUpServerSecs": "60-180",
        }
    return {
        "mode": "packet-up",
        "path": path,
        "uplinkHTTPMethod": "GET",
        "xPaddingHeader": "X-Cache",
        "xPaddingKey": "_dc",
        "xPaddingMethod": "tokenish",
        "xPaddingObfsMode": True,
        "xPaddingPlacement": "queryInHeader",
        "uplinkDataPlacement": "header",
        "uplinkDataKey": "X-Request-Trace",
        "scMaxEachPostBytes": 24000,
        "scMinPostsIntervalMs": 10,
        "serverMaxHeaderBytes": 65536,
        "noSSEHeader": False,
    }


def build_profile(spec: InstallSpec, artifacts: dict) -> dict:
    """Собирает профиль транспорта, каскада и дополнений."""
    inbound = {
        "tag": artifacts.get("inbound_tag")
        or spec.name.replace(" ", "_")
        + "_"
        + spec.transport.upper()
        + ("_" + artifacts["install_id"][:10] if artifacts.get("install_id") else ""),
        "listen": "::" if ":" in spec.address else "0.0.0.0",
        "port": spec.client_port,
        "protocol": "vless",
        "settings": {"clients": [], "decryption": "none"},
        "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]},
    }
    if spec.transport.startswith("cdn_"):
        path = artifacts.get("xhttp_path")
        if not path:
            raise InstallError("profile", "Не сохранён путь XHTTP")
        extra = copy.deepcopy(artifacts.get("xhttp_extra") or _cdn_extra(spec.transport, path))
        artifacts["xhttp_extra"] = extra
        inbound.update(listen="127.0.0.1", port=8443 if spec.transport == "cdn_vk" else 4443)
        inbound["streamSettings"] = {
            "network": "xhttp",
            "security": "none",
            "xhttpSettings": {"mode": "packet-up", "path": path, "extra": extra},
        }
        if spec.transport == "cdn_vk":
            inbound["sniffing"]["routeOnly"] = True
            inbound["streamSettings"]["xhttpSettings"].update(host="", noSSEHeader=True)
    elif spec.transport == "vless_encryption":
        decryption = artifacts.get("decryption")
        if not decryption:
            raise InstallError("keys", "Не получен ключ VLESS Encryption")
        inbound["settings"]["decryption"] = decryption
        inbound["streamSettings"] = {"network": "raw", "security": "none"}
    else:
        if not artifacts.get("private_key") or not artifacts.get("short_id"):
            raise InstallError("keys", "Ключи Reality не сохранены")
        selfsteal = spec.transport == "vless_tcp_selfsteal"
        reality = {
            "show": False,
            "dest": "127.0.0.1:8443" if selfsteal else spec.reality_domain + ":443",
            "serverNames": [spec.domain if selfsteal else spec.reality_domain],
            "privateKey": artifacts["private_key"],
            "shortIds": [artifacts["short_id"]],
        }
        inbound["streamSettings"] = {"network": "tcp", "security": "reality", "realitySettings": reality}
        if selfsteal:
            reality["xver"] = 1
        elif spec.transport == "vless_xhttp_reality":
            reality["target"] = reality.pop("dest")
            reality.update(xver=0, fingerprint="firefox")
            inbound["streamSettings"].update(
                network="xhttp", xhttpSettings={"host": spec.reality_domain, "mode": "auto", "path": "/"}
            )

    domain_strategy = "UseIPv6" if spec.exit_ip and ipaddress.ip_address(spec.exit_ip).version == 6 else "UseIPv4"
    direct = {"tag": "DIRECT", "protocol": "freedom", "settings": {"domainStrategy": domain_strategy}}
    if spec.exit_ip:
        direct["sendThrough"] = spec.exit_ip
    outbounds = [direct, {"tag": "BLOCK", "protocol": "blackhole"}]
    rules = []
    installed = set(artifacts.get("installed_addons") or [])
    if "antitorrent" in installed:
        rules.append({"type": "field", "protocol": ["bittorrent"], "outboundTag": "BLOCK"})
    for addon, port, domains in [("psiphon", 40080, _PSIPHON_DOMAINS), ("warp", 40000, _WARP_DOMAINS)]:
        if addon in installed:
            outbounds.append({
                "tag": addon + "-out",
                "protocol": "socks",
                "settings": {"servers": [{"address": "127.0.0.1", "port": port}]},
            })
            rules.append({"type": "field", "domain": list(domains), "outboundTag": addon + "-out"})
    if spec.role == "entry":
        cascade = artifacts.get("cascade") or {}
        required = ("address", "port", "uuid", "public_key", "short_id", "server_name")
        if any(not cascade.get(name) for name in required):
            raise InstallError("cascade", "Не подтверждены параметры выходной ноды", retryable=False)
        outbounds.insert(
            0,
            {
                "tag": "CASCADE",
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": cascade["address"],
                            "port": int(cascade["port"]),
                            "users": [{"id": cascade["uuid"], "encryption": "none", "flow": "xtls-rprx-vision"}],
                        }
                    ]
                },
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "realitySettings": {
                        "serverName": cascade["server_name"],
                        "publicKey": cascade["public_key"],
                        "shortId": cascade["short_id"],
                        "fingerprint": "firefox",
                    },
                },
            },
        )
        rules.append({"type": "field", "inboundTag": [inbound["tag"]], "outboundTag": "CASCADE"})
    profile = {
        "log": {"loglevel": "warning"},
        "inbounds": [inbound],
        "outbounds": outbounds,
        "dns": {"servers": ["1.1.1.1", "8.8.8.8", "77.88.8.8", "localhost"], "queryStrategy": domain_strategy},
    }
    if rules:
        profile["routing"] = {"domainStrategy": "AsIs", "rules": rules}
    return profile
