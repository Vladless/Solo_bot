
import requests
from config import GET_INBOUNDS_URL, AUTH_URL, DOMEN
import json

def login_with_credentials(username, password):
    auth_url = AUTH_URL
    data = {
        "username": username,
        "password": password
    }
    response = requests.post(auth_url, json=data)
    if response.status_code == 200:
        session = requests.Session()
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

def link(session, user_id: str):
    """
    Получение ссылки для подключения по ID пользователя.
    :param session: requests.Session - авторизованная сессия
    :param user_id: str - идентификатор пользователя (email или другой параметр)
    :return: str - ссылка для подключения
    """
    client_id = ''
    
    # Получаем список клиентов через API
    response = get_clients(session)
    
    if 'obj' not in response or len(response['obj']) == 0:
        raise Exception("Не удалось получить данные клиентов.")

    # Извлекаем данные клиента
    inbounds = response['obj'][0]  # Убедитесь, что это правильный индекс
    settings = json.loads(inbounds['settings'])
    
    # Ищем клиента по user_id
    for client in settings["clients"]:
        if client['email'] == user_id:
            client_id = client["id"]
            break
    
    if not client_id:
        raise Exception(f"Клиент с идентификатором {user_id} не найден.")
    
    # Получаем streamSettings и генерируем ссылку
    stream_settings = json.loads(inbounds['streamSettings'])
    tcp = stream_settings.get('network', 'tcp')  # Предположим, по умолчанию 'tcp'
    reality = stream_settings.get('security', 'reality')  # Предположим, по умолчанию 'reality'
    flow = stream_settings.get('flow', 'xtls-rprx-vision')  # Предположим, по умолчанию 'xtls-rprx-vision'
    email = user_id

    # Создание ссылки для подключения VLESS
    val = f"vless://{client_id}@{DOMEN}?type={tcp}&security={reality}&fp=chrome&pbk=ZIMoEnEd8-qMJkReVU5JxbiEj8CCSrvpm_ckvJ-46TE&sni=yahoo.com&sid=0fb4b595&spx=%2F&flow={flow}#VPN_F-{email}"
    
    return val

def get_statistics(session, tg_id: int):
    # Пример URL для получения статистики
    STATS_URL = f"https://vpn.pocomacho.ru:34268/solonet/panel/api/inbounds/stats/{tg_id}"
    response = session.get(STATS_URL)
    if response.status_code == 200:
        stats = response.json()
        # Форматирование статистики для отображения
        return f"Загрузка: {stats['upload']} MB\nВывод: {stats['download']} MB"
    else:
        raise Exception(f"Ошибка при получении статистики: {response.status_code}, {response.text}")
