import sys

from settings import cache_config, config


sys.modules.setdefault("config", config)
sys.modules.setdefault("core.cache_config", cache_config)

from settings import buttons, texts


sys.modules.setdefault("handlers.texts", texts)
sys.modules.setdefault("handlers.buttons", buttons)
