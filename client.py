import json
import aiohttp

from config import SERVERS 


async def add_client(session, server_id: str, client_id: str, email: str, tg_id: str, limit_ip: int, total_gb: int, expiry_time: int, enable: bool, flow: str):
    api_url = SERVERS[server_id]['API_URL']
    url = f'{api_url}/panel/api/inbounds/addClient'
    
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

    async with session.post(url, json=data, headers=headers) as response:
        print(f"Запрос на добавление клиента: {data}")
        print(f"Статус ответа: {response.status}")
        response_text = await response.text()
        print(f"Ответ от сервера: {response_text}")

        if response.status == 200:
            print(f"Клиент добавлен: email={email}")
            return await response.json()
        else:
            print(f"Ошибка при добавлении клиента: {response.status}, {response_text}")
            return None


async def extend_client_key(session, server_id: str, tg_id, client_id, email: str, new_expiry_time: int) -> bool:
    api_url = SERVERS[server_id]['API_URL']
    
    async with session.get(f"{api_url}/panel/api/inbounds/getClientTraffics/{email}") as response:
        print(f"GET {response.url} Status: {response.status}")
        response_text = await response.text()
        print(f"GET Response: {response_text}")
        
        if response.status != 200:
            print(f"Ошибка при получении данных клиента: {response.status} - {response_text}")
            return False
        
        client_data = (await response.json()).get("obj", {})
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
            async with session.post(f"{api_url}/panel/api/inbounds/updateClient/{client_id}", json=payload, headers=headers) as response:
                print(f"POST {response.url} Status: {response.status}")
                print(f"POST Request Data: {json.dumps(payload, indent=2)}")
                response_text = await response.text()
                print(f"POST Response: {response_text}")
                
                if response.status == 200:
                    return True
                else:
                    print(f"Ошибка при продлении ключа: {response.status} - {response_text}")
                    return False
        except Exception as e:
            print(f"Ошибка запроса: {e}")
            return False


async def delete_client(session, server_id: str, client_id: str) -> bool:
    api_url = SERVERS[server_id]['API_URL']
    url = f"{api_url}/panel/api/inbounds/1/delClient/{client_id}"
    headers = {
        'Accept': 'application/json'
    }

    try:
        async with session.post(url, headers=headers) as response:
            if response.status == 200:
                return True
            else:
                print(f"Ошибка при удалении клиента: {response.status} - {await response.text()}")
                return False
    except Exception as e:
        print(f"Ошибка запроса: {e}")
        return False
