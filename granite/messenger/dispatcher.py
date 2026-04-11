"""Messenger dispatcher: выбрать sender + шаблон/текст + залогировать touch."""
import os
from datetime import datetime, timezone
from loguru import logger

from granite.messenger.base import SendResult
from granite.messenger.tg_sender import TgSender
from granite.messenger.wa_sender import WaSender
from granite.database import CrmTemplateRow, CrmTouchRow, CrmContactRow


class MessengerDispatcher:
    """Отправка сообщений через мессенджеры с logging в CRM."""

    def __init__(self):
        self.tg = TgSender()
        self.wa = WaSender()
        self.from_name = os.environ.get("FROM_NAME", "")

    def send(
        self,
        channel: str,
        contact_id: str,
        template: CrmTemplateRow | None = None,
        text: str = "",
        company_name: str = "",
        city: str = "",
        db_session=None,
        company_id: int | None = None,
    ) -> SendResult:
        """Отправить сообщение через мессенджер.

        Args:
            channel: "tg" или "wa"
            contact_id: username TG или номер телефона WA
            template: ORM-объект шаблона (если text не передан — рендерит из шаблона)
            text: готовый текст сообщения (приоритетнее template)
            company_name: название компании (для плейсхолдеров)
            city: город (для плейсхолдеров)
            db_session: SQLAlchemy session. Если передана — логирует touch.
            company_id: ID компании для touch.

        Returns:
            SendResult
        """
        sender = {"tg": self.tg, "wa": self.wa}.get(channel)
        if not sender:
            return SendResult(
                success=False, channel=channel, contact_id=contact_id,
                error=f"Unknown channel: {channel}",
            )

        # Определяем текст: прямой приоритетнее шаблона
        if text:
            message = text
        elif template:
            render_kwargs = {
                "from_name": self.from_name,
                "city": city,
                "company_name": company_name,
            }
            message = template.render(**render_kwargs)
        else:
            return SendResult(
                success=False, channel=channel, contact_id=contact_id,
                error="No text or template provided",
            )

        # Отправка
        result = sender.send(contact_id, message)

        # Логирование в CRM (если передана сессия)
        if db_session is not None and company_id is not None and result.success:
            touch = CrmTouchRow(
                company_id=company_id,
                channel=channel,
                direction="outgoing",
                body=message,
                note=f"[{channel.upper()} mock sent to {result.contact_id}]",
            )
            db_session.add(touch)

            contact = db_session.get(CrmContactRow, company_id)
            if contact:
                now = datetime.now(timezone.utc)
                contact.contact_count = (contact.contact_count or 0) + 1
                contact.last_contact_at = now
                contact.last_contact_channel = channel
                if not contact.first_contact_at:
                    contact.first_contact_at = now
                if channel == "tg":
                    contact.tg_sent_count = (contact.tg_sent_count or 0) + 1
                    contact.last_tg_at = now
                    if contact.funnel_stage in ("new", "email_sent", "email_opened"):
                        contact.funnel_stage = "tg_sent"
                elif channel == "wa":
                    contact.wa_sent_count = (contact.wa_sent_count or 0) + 1
                    contact.last_wa_at = now
                    if contact.funnel_stage not in ("replied", "interested", "not_interested"):
                        contact.funnel_stage = "wa_sent"

        return result
