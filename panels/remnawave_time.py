import aiohttp
from typing import List, Dict, Any
from logger import logger
from config import REMNAWAVE_TOKEN_LOGIN_ENABLED, REMNAWAVE_ACCESS_TOKEN, REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD


async def login_remnawave(api_url: str, username: str, password: str) -> str | None:
    if REMNAWAVE_TOKEN_LOGIN_ENABLED and REMNAWAVE_ACCESS_TOKEN:
        logger.info("[Remnawave API] Используется авторизация по токену")
        return REMNAWAVE_ACCESS_TOKEN
    
    async with aiohttp.ClientSession() as session:
        auth_data = {
            "username": username,
            "password": password
        }
        
        try:
            auth_response = await session.post(f"{api_url}/auth/login", json=auth_data)
            
            if auth_response.status != 200:
                logger.error(f"[Remnawave API] Ошибка HTTP статуса: {auth_response.status}")
                return None
            
            auth_result = await auth_response.json()
            token = None
            if auth_result.get("success") and auth_result.get("data", {}).get("token"):
                token = auth_result.get("data", {}).get("token")
            elif auth_result.get("response", {}).get("accessToken"):
                token = auth_result.get("response", {}).get("accessToken")
            elif auth_result.get("token"):
                token = auth_result.get("token")
            
            if not token:
                logger.error(f"[Remnawave API] Токен не найден в ответе")
            
            return token
            
        except Exception as e:
            logger.error(f"[Remnawave API] Ошибка при авторизации: {e}")
            return None


async def get_all_users_time(api_url: str, username: str, password: str) -> List[Dict[str, Any]]:
    all_users = []
    page_size = 250
    start = 0

    token = await login_remnawave(api_url, username, password)
    if not token:
        logger.error("[Remnawave API] Не удалось получить токен авторизации")
        return []

    headers = {"Authorization": f"Bearer {token}"}

    async with aiohttp.ClientSession() as session:
        while True:
            params = {
                "size": page_size,
                "start": start
            }
            
            users_endpoint = f"{api_url}/users"
            
            users_response = await session.get(users_endpoint, params=params, headers=headers)
            
            if users_response.status != 200:
                break
            
            users_result = await users_response.json()
            
            if users_result.get("success") is False:
                break
            
            users_data = None
            if "response" in users_result:
                users_data = users_result.get("response", {})
            elif "data" in users_result:
                users_data = users_result.get("data", {})
            else:
                users_data = users_result
            
            if not users_data:
                break
            
            users = users_data.get("users", [])
            total = users_data.get("total", 0)
            
            if not users:
                break
            
            all_users.extend(users)
            start += len(users)
            
            if len(users) < page_size or start >= total:
                break
        
        return all_users 