import aiohttp

from config import (
    REMNAWAVE_ACCESS_TOKEN,
    REMNAWAVE_LOGIN,
    REMNAWAVE_PASSWORD,
    REMNAWAVE_TOKEN_LOGIN_ENABLED,
)
from logger import logger

PANEL_REMNA = "[Remnawave]"


async def revoke_user_subscription(
    api_url: str,
    user_uuid: str,
    short_uuid: str | None = None,
) -> dict | None:
    base_url = api_url.rstrip("/")
    
    async with aiohttp.ClientSession() as session:
        if REMNAWAVE_TOKEN_LOGIN_ENABLED and REMNAWAVE_ACCESS_TOKEN:
            token = REMNAWAVE_ACCESS_TOKEN
        else:
            login_url = f"{base_url}/auth/login"
            login_data = {"username": REMNAWAVE_LOGIN, "password": REMNAWAVE_PASSWORD}
            
            try:
                async with session.post(login_url, json=login_data) as resp:
                    if resp.status != 200 and resp.status != 201:
                        logger.error(f"{PANEL_REMNA} Авторизация не удалась: {resp.status}")
                        return None
                    
                    auth_response = await resp.json()
                    token = auth_response.get("response", {}).get("accessToken")
                    if not token:
                        logger.error(f"{PANEL_REMNA} Не получен accessToken")
                        return None
            except Exception as e:
                logger.error(f"{PANEL_REMNA} Ошибка авторизации: {e}")
                return None

        revoke_url = f"{base_url}/users/{user_uuid}/actions/revoke"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        
        revoke_data = {}
        if short_uuid:
            revoke_data["shortUuid"] = short_uuid
        
        try:
            async with session.post(revoke_url, json=revoke_data, headers=headers) as resp:
                if resp.status != 200 and resp.status != 201:
                    error_text = await resp.text()
                    logger.error(f"{PANEL_REMNA} Revoke не удался: {resp.status} - {error_text}")
                    return None
                
                result = await resp.json()
                user_data = result.get("response", {})
                
                logger.info(f"{PANEL_REMNA} Подписка {user_uuid} успешно отозвана")
                return user_data
                
        except Exception as e:
            logger.error(f"{PANEL_REMNA} Ошибка revoke: {e}")
            return None
