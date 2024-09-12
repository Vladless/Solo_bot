
import json
import uuid
from config import ADD_CLIENT_URL

def add_client(session, client_id, email, tg_id, limit_ip, total_gb, expiry_time, enable, flow):
    settings = {
        "clients": [
            {
                "id": client_id,
                "alterId": 0,
                "email": email,
                "limitIp": limit_ip,
                "totalGB": total_gb,
                "expiryTime": expiry_time,
                "enable": enable,
                "tgId": tg_id,
                "subId": "",
                "flow": flow
            }
        ]
    }
    
    data = {
        "id": 1,  # Можно изменить в зависимости от ваших требований
        "settings": json.dumps(settings)
    }
    
    headers = {
        "Accept": "application/json"
    }
    
    response = session.post(ADD_CLIENT_URL, headers=headers, json=data)
    
    if response.status_code == 200:
        return response.json()
    else:
        raise Exception(f"Ошибка при добавлении клиента: {response.status_code}, {response.text}")

def generate_client_id():
    return str(uuid.uuid4())
