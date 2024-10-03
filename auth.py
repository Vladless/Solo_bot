import json

import requests

from config import SERVERS

session = None

def login_with_credentials(server_id: str, username: str, password: str):
    global session
    session = requests.Session()
    api_url = SERVERS[server_id]['API_URL']
    auth_url = f"{api_url}/login/"
    data = {
        "username": username,
        "password": password
    }
    response = requests.post(auth_url, json=data)
    if response.status_code == 200:
        session.cookies.update(response.cookies)
        return session
    else:
        raise Exception(f"Ошибка авторизации: {response.status_code}, {response.text}")

def get_clients(session, server_id):
    api_url = SERVERS[server_id]['API_URL']
    response = session.get(f'{api_url}/panel/api/inbounds/list/')
    if response.status_code == 200:
        return response.json()
    else:
        raise Exception(f"Ошибка при получении клиентов: {response.status_code}, {response.text}")

def link(session, server_id: str, client_id: str, email: str):
    """
    Получение ссылки для подключения по ID клиента.
    :param server_id: str - идентификатор сервера
    :param client_id: str - идентификатор клиента
    :param email: str - электронная почта клиента
    :return: str - ссылка для подключения
    """
    response = get_clients(session, server_id)
    
    if 'obj' not in response or len(response['obj']) == 0:
        raise Exception("Не удалось получить данные клиентов.")
    
    inbounds = response['obj'][0]
    settings = json.loads(inbounds['settings'])
    
    stream_settings = json.loads(inbounds['streamSettings'])
    tcp = stream_settings.get('network', 'tcp')
    reality = stream_settings.get('security', 'reality')
    flow = stream_settings.get('flow', 'xtls-rprx-vision')
    
    val = f"vless://{client_id}@{SERVERS[server_id]['DOMEN']}?type={tcp}&security={reality}&pbk={SERVERS[server_id]['PBK']}&fp=chrome&sni={SERVERS[server_id]['SNI']}&sid={SERVERS[server_id]['SID']}=%2F&flow={flow}#{SERVERS[server_id]['PREFIX']}-{email}"
    return val
