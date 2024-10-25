import re
def sanitize_key_name(key_name: str) -> str:
    return re.sub(r'[^a-z0-9@._-]', '', key_name.lower())