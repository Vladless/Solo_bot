import json

import aiohttp

from config import SERVERS

session = None


async def login_with_credentials(server_id: str, username: str, password: str):
    global session
    session = aiohttp.ClientSession()
    api_url = SERVERS[server_id]["API_URL"]
    auth_url = f"{api_url}/login/"

    data = {"username": username, "password": password}

    async with session.post(auth_url, json=data) as response:
        if response.status == 200:
            session.cookie_jar.update_cookies(response.cookies)
            return session
        else:
            raise Exception(
                f"Ошибка авторизации: {response.status}, {await response.text()}"
            )


async def get_clients(session, server_id):
    api_url = SERVERS[server_id]["API_URL"]
    async with session.get(f"{api_url}/panel/api/inbounds/list/") as response:
        if response.status == 200:
            return await response.json()
        else:
            raise Exception(
                f"Ошибка при получении клиентов: {response.status}, {await response.text()}"
            )


async def link(session, server_id: str, client_id: str, email: str):
    """
    Получение ссылки для подключения по ID клиента.
    :param server_id: str - идентификатор сервера
    :param client_id: str - идентификатор клиента
    :param email: str - электронная почта клиента
    :return: str - ссылка для подключения
    """
    response = await get_clients(session, server_id)

    if "obj" not in response or len(response["obj"]) == 0:
        raise Exception("Не удалось получить данные клиентов.")

    inbounds = response["obj"][0]

    stream_settings = json.loads(inbounds["streamSettings"])
    tcp = stream_settings.get("network", "tcp")
    reality = stream_settings.get("security", "reality")
    flow = stream_settings.get("flow", "xtls-rprx-vision")

    val = (
        f"vless://{client_id}@{SERVERS[server_id]['DOMEN']}?type={tcp}&security={reality}&pbk={SERVERS[server_id]['PBK']}"
        f"&fp=chrome&sni={SERVERS[server_id]['SNI']}&sid={SERVERS[server_id]['SID']}=%2F&flow={flow}#{SERVERS[server_id]['PREFIX']}-{email}"
    )
    return val


async def link_subscription(email, server_id):
    server = SERVERS.get(server_id)
    if server:
        subscription_url = f"{server['SUBSCRIPTION']}/{email}"
        return subscription_url
    else:
        raise ValueError(f"Server '{server_id}' not found in configuration.")
