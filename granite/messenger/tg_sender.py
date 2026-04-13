"""Telegram sender — mock (dry-run).

Замена на Telethon/Pyrogram будет в отдельной фазе.
Текущая реализация: логирует отправку, НЕ отправляет реально.
"""
import os
from loguru import logger

from granite.messenger.base import BaseMessenger, SendResult


class TgSender(BaseMessenger):
    """Mock Telegram sender. Логирует и возвращает success=True."""

    def __init__(self):
        # Заглушки для будущей Telethon-сессии
        self.session_path = os.environ.get("TG_SESSION_PATH", "data/tg_session")
        self.api_id = int(os.environ.get("TELEGRAM_API_ID", "0"))
        self.api_hash = os.environ.get("TELEGRAM_API_HASH", "")

    def send(self, contact_id: str, text: str) -> SendResult:
        """Отправить в Telegram (mock).

        Args:
            contact_id: username без @ (например "granit_master")
                        или ссылка "t.me/granit_master"
            text: текст сообщения
        """
        # Нормализация: убрать "https://t.me/" или "@"
        username = contact_id
        for prefix in ("https://t.me/", "http://t.me/", "t.me/", "@"):
            if username.startswith(prefix):
                username = username[len(prefix):]
                break

        # Валидация
        if not username or len(username) < 2:
            return SendResult(
                success=False, channel="tg", contact_id=contact_id,
                error=f"Invalid Telegram username: {contact_id!r}"
            )

        # MOCK: логируем вместо реальной отправки
        logger.info(f"[TG MOCK] -> @{username}: {text[:80]}...")
        logger.debug(f"[TG MOCK] full text: {text}")

        return SendResult(success=True, channel="tg", contact_id=username)
