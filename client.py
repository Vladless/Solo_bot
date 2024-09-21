import json

def add_client(session, client_id: str, email: str, tg_id: str, limit_ip: int, total_gb: int, expiry_time: int, enable: bool, flow: str):
    url = 'https://solonet.pocomacho.ru:62553/solonet/panel/api/inbounds/addClient'
    
    email = email.lower()
    
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
    
    settings = json.dumps({"clients": [client_data]})

    data = {
        "id": 1,
        "settings": settings
    }

    headers = {
        'Content-Type': 'application/json',
    }
    
    response = session.post(url, json=data, headers=headers)
    
    print(f"Запрос на добавление клиента: {data}")
    print(f"Статус ответа: {response.status_code}")
    print(f"Ответ от сервера: {response.text}")

    if response.status_code == 200:
        print(f"Клиент добавлен: email={email}")
    else:
        print(f"Ошибка при добавлении клиента: {response.status_code}, {response.text}")

def extend_client_key(session, tg_id, client_id, email: str, new_expiry_time: int) -> bool:
    response = session.get(f"https://solonet.pocomacho.ru:62553/solonet/panel/api/inbounds/getClientTraffics/{email}")
    print(f"GET {response.url} Status: {response.status_code}")
    print(f"GET Response: {response.text}")
    
    if response.status_code != 200:
        print(f"Ошибка при получении данных клиента: {response.status_code} - {response.text}")
        return False
    
    client_data = response.json().get("obj", {})
    print(client_data)

    if not client_data:
        print("Не удалось получить данные клиента.")
        return False

    current_expiry_time = client_data.get('expiryTime', 0)
    
    if current_expiry_time == 0:
        current_expiry_time = new_expiry_time

    updated_expiry_time = max(current_expiry_time, new_expiry_time)

    payload = {
        "id": 1,
        "settings": json.dumps({
            "clients": [
                {
                    "id": client_id,
                    "alterId": 0,
                    "email": email.lower(),
                    "limitIp": 2,
                    "totalGB": 429496729600000,
                    "expiryTime": updated_expiry_time,
                    "enable": True,
                    "tgId": tg_id,
                    "subId": "",
                    "flow": "xtls-rprx-vision"
                }
            ]
        })
    }
    
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }
    
    try:
        response = session.post(f"https://solonet.pocomacho.ru:62553/solonet/panel/api/inbounds/updateClient/{client_id}", json=payload, headers=headers)
        print(f"POST {response.url} Status: {response.status_code}")
        print(f"POST Request Data: {json.dumps(payload, indent=2)}")
        print(f"POST Response: {response.text}")
        
        if response.status_code == 200:
            return True
        else:
            print(f"Ошибка при продлении ключа: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"Ошибка запроса: {e}")
        return False

def delete_client(session, client_id: str) -> bool:
    url = f"https://solonet.pocomacho.ru:62553/solonet/panel/api/inbounds/1/delClient/{client_id}"
    headers = {
        'Accept': 'application/json'
    }
    try:
        response = session.post(url, headers=headers)
        if response.status_code == 200:
            return True
        else:
            print(f"Ошибка при удалении клиента: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"Ошибка запроса: {e}")
        return False