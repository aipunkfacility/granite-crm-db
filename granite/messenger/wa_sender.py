"""WhatsApp sender — mock (dry-run).

Замена на Baileys/whatsapp-http API будет в отдельной фазе.
Текущая реализация: логирует отправку, НЕ отправляет реально.
"""
import os
from loguru import logger

from granite.messenger.base import BaseMessenger, SendResult


class WaSender(BaseMessenger):
    """Mock WhatsApp sender. Логирует и возвращает success=True."""

    def __init__(self):
        # Заглушки для будущей WA API
        self.api_url = os.environ.get("WA_API_URL", "http://localhost:3000")
        self.api_token = os.environ.get("WA_API_TOKEN", "")

    def send(self, contact_id: str, text: str) -> SendResult:
        """Отправить в WhatsApp (mock).

        Args:
            contact_id: номер телефона (например "79001234567")
                        или ссылка "wa.me/79001234567" или "https://wa.me/79001234567"
            text: текст сообщения
        """
        # Нормализация телефона
        phone = contact_id
        for prefix in ("https://wa.me/", "http://wa.me/", "wa.me/"):
            if phone.startswith(prefix):
                phone = phone[len(prefix):]
                break
        phone = phone.replace("-", "").replace(" ", "").replace("(", "").replace(")", "")

        # Валидация
        if not phone or len(phone) < 10:
            return SendResult(
                success=False, channel="wa", contact_id=contact_id,
                error=f"Invalid WhatsApp phone: {contact_id!r}"
            )

        # MOCK: логируем вместо реальной отправки
        logger.info(f"[WA MOCK] -> +{phone}: {text[:80]}...")
        logger.debug(f"[WA MOCK] full text: {text}")

        return SendResult(success=True, channel="wa", contact_id=phone)
