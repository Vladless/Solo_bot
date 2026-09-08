import services.admin_alert


async def _no_admin_alert(text: str) -> bool:
    return False


services.admin_alert.send_admin_alert = _no_admin_alert
