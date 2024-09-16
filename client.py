import requests
import json
from config import ADMIN_USERNAME, ADMIN_PASSWORD, GET_INBOUNDS_URL
import uuid

import requests
from config import ADMIN_USERNAME, ADMIN_PASSWORD

import requests
from config import ADMIN_USERNAME, ADMIN_PASSWORD

def add_client(session, client_id: str, email: str, tg_id: str, limit_ip: int, total_gb: int, expiry_time: int, enable: bool, flow: str):
    url = 'https://solonet.pocomacho.ru:62553/solonet/panel/api/inbounds/addClient'
    
    # Преобразуем email в нижний регистр
    email = email.lower()
    
    # Формируем данные клиента
    client_data = {
        "id": client_id,
        "alterId": 0,
        "email": email,
        "limitIp": limit_ip,
        "totalGB": total_gb,
        "expiryTime": expiry_time,
        "enable": enable,
        "tgId": tg_id,
        "subId": "",
        "flow": flow,
    }
    
    # Преобразуем данные клиента в строку JSON
    settings = json.dumps({"clients": [client_data]})

    # Формируем тело запроса
    data = {
        "id": 1,  # Если id динамически изменяется, замените это значение
        "settings": settings
    }

    headers = {
        'Content-Type': 'application/json', # Если требуется токен авторизации
    }
    
    response = session.post(url, json=data, headers=headers)
    
    # Выводим информацию о запросе и ответе
    print(f"Запрос на добавление клиента: {data}")
    print(f"Статус ответа: {response.status_code}")
    print(f"Ответ от сервера: {response.text}")

    if response.status_code == 200:
        print(f"Клиент добавлен: email={email}")
    else:
        print(f"Ошибка при добавлении клиента: {response.status_code}, {response.text}")



def generate_client_id():
    return str(uuid.uuid4())
