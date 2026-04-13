"""Base classes for messenger senders."""
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SendResult:
    success: bool
    channel: str           # "tg" or "wa"
    contact_id: str        # username, phone, chat_id
    error: str = ""
    message_id: str = ""   # пустой для mock, реальный для продакшена


class BaseMessenger(ABC):
    """Базовый класс для мессенджер-сендера."""

    @abstractmethod
    def send(self, contact_id: str, text: str) -> SendResult:
        """Отправить сообщение. Возвращает SendResult."""
        ...
