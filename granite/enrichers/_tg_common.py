# enrichers/_tg_common.py — общие константы для Telegram-запросов
# Дефолтные значения; используются если конфиг не содержит enrichment.tg_finder
TG_MAX_RETRIES = 5
TG_INITIAL_BACKOFF = 5  # seconds


def get_tg_config(config: dict) -> dict:
    """Извлечь Telegram-конфиг из конфига с дефолтами."""
    tg = config.get("enrichment", {}).get("tg_finder", {})
    return {
        "check_delay": tg.get("check_delay", 1.5),
        "max_retries": tg.get("max_retries", TG_MAX_RETRIES),
        "initial_backoff": tg.get("initial_backoff", TG_INITIAL_BACKOFF),
        "request_timeout": tg.get("request_timeout", 10),
    }
