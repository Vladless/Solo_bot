import requests
from config import ADMIN_USERNAME, ADMIN_PASSWORD, GET_INBOUNDS_URL
import json

session = None

def login_with_credentials(username, password):
    global session
    session = requests.Session()
    auth_url = "https://solonet.pocomacho.ru:62553/solonet/login/"
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

def get_clients(session):
    response = session.get(GET_INBOUNDS_URL)
    if response.status_code == 200:
        return response.json()  # Возвращает данные по инбаундам и клиентам
    else:
        raise Exception(f"Ошибка при получении клиентов: {response.status_code}, {response.text}")

def link(session, client_id: str):
    """
    Получение ссылки для подключения по ID клиента.
    :param session: requests.Session - авторизованная сессия
    :param client_id: str - идентификатор клиента
    :return: str - ссылка для подключения
    """
    response = get_clients(session)
    
    if 'obj' not in response or len(response['obj']) == 0:
        raise Exception("Не удалось получить данные клиентов.")
    
    inbounds = response['obj'][0]  # Убедитесь, что это правильный индекс
    settings = json.loads(inbounds['settings'])
    
    # Найти клиентский ID в настройках
    stream_settings = json.loads(inbounds['streamSettings'])
    tcp = stream_settings.get('network', 'tcp')
    reality = stream_settings.get('security', 'reality')
    flow = stream_settings.get('flow', 'xtls-rprx-vision')
    
    # Создание ссылки для подключения VLESS
    val = f"vless://{client_id}@solonet.pocomacho.ru:443?type={tcp}&security={reality}&fp=chrome&pbk=Ik3cfHdM5RXYCuzTxhaFfo58Fzwjvw11AtKsz5-izxA&fp=chrome&sni=discord.com&sid=6fc9&spx=%2F&flow={flow}#VPN_F-{client_id}"
    return val

