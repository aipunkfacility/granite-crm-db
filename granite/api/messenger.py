"""Messenger API: отправка через TG/WA."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from granite.api.deps import get_db
from granite.api.schemas import SendMessageRequest
from granite.database import CompanyRow, EnrichedCompanyRow, CrmTemplateRow
from granite.messenger.dispatcher import MessengerDispatcher

__all__ = ["router"]
router = APIRouter()


@router.post("/companies/{company_id}/send")
def send_message(
    company_id: int,
    data: SendMessageRequest,
    db: Session = Depends(get_db),
):
    """Отправить сообщение через мессенджер.

    Body:
        channel: "tg" | "wa"
        template_name: имя шаблона (опционально, если передан text)
        text: текст сообщения (опционально, если передан template_name)
    """
    channel = data.channel

    company = db.get(CompanyRow, company_id)
    if not company:
        raise HTTPException(404, "Company not found")

    enriched = db.get(EnrichedCompanyRow, company_id)
    messengers = enriched.messengers or {} if enriched else {}

    # Определяем контакт ID
    if channel == "tg":
        contact_id = messengers.get("telegram")
        if not contact_id:
            raise HTTPException(400, "No Telegram contact for this company")
    elif channel == "wa":
        contact_id = messengers.get("whatsapp")
        if not contact_id:
            raise HTTPException(400, "No WhatsApp contact for this company")

    # Определяем текст
    if not data.text and not data.template_name:
        raise HTTPException(400, "Provide 'text' or 'template_name'")

    if data.template_name and not data.text:
        template = db.query(CrmTemplateRow).filter_by(name=data.template_name).first()
        if not template:
            raise HTTPException(404, f"Template not found: {data.template_name}")

    disp = MessengerDispatcher()

    # Если template_name указан и text не передан — передаём template, dispatcher рендерит
    # Если text передан напрямую — передаём text
    if data.template_name and not data.text:
        result = disp.send(
            channel=channel,
            contact_id=contact_id,
            template=template,
            company_name=company.name_best or "",
            city=company.city or "",
            db_session=db,
            company_id=company_id,
        )
    else:
        result = disp.send(
            channel=channel,
            contact_id=contact_id,
            text=data.text,
            db_session=db,
            company_id=company_id,
        )

    return {
        "ok": result.success,
        "channel": result.channel,
        "contact_id": result.contact_id,
        "error": result.error,
    }
