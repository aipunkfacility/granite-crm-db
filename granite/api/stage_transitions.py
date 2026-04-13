"""Stage-переходы при касаниях — единый источник правды."""
from datetime import datetime, timezone


def apply_outgoing_touch(contact, channel: str) -> None:
    """Обновить счётчики и stage при исходящем касании.

    Вызывается из touches.py и messenger/dispatcher.py.
    НЕ делает commit/flush — вызывающая функция отвечает за транзакцию.
    """
    now = datetime.now(timezone.utc)
    contact.contact_count = (contact.contact_count or 0) + 1
    contact.last_contact_at = now
    contact.last_contact_channel = channel
    if not contact.first_contact_at:
        contact.first_contact_at = now

    if channel == "email":
        contact.email_sent_count = (contact.email_sent_count or 0) + 1
        contact.last_email_sent_at = now
        if contact.funnel_stage == "new":
            contact.funnel_stage = "email_sent"
    elif channel == "tg":
        contact.tg_sent_count = (contact.tg_sent_count or 0) + 1
        contact.last_tg_at = now
        if contact.funnel_stage in ("new", "email_sent", "email_opened"):
            contact.funnel_stage = "tg_sent"
    elif channel == "wa":
        contact.wa_sent_count = (contact.wa_sent_count or 0) + 1
        contact.last_wa_at = now
        if contact.funnel_stage not in ("replied", "interested", "not_interested"):
            contact.funnel_stage = "wa_sent"


def apply_incoming_touch(contact) -> None:
    """Обновить stage при входящем касании (ответ клиента)."""
    now = datetime.now(timezone.utc)
    contact.contact_count = (contact.contact_count or 0) + 1
    contact.last_contact_at = now
    contact.stop_automation = 1
    if contact.funnel_stage not in ("interested", "not_interested"):
        contact.funnel_stage = "replied"
