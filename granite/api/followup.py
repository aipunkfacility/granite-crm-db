"""Follow-up очередь: кому нужно написать сегодня."""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from granite.api.deps import get_db
from granite.database import CompanyRow, EnrichedCompanyRow, CrmContactRow

__all__ = ["router"]

router = APIRouter()

# Сколько дней ждать после касания перед следующим
STAGE_NEXT_ACTION = {
    "new": {"days": 0, "channel": "email", "template": "cold_email_1", "action": "Отправить холодное письмо"},
    "email_sent": {"days": 4, "channel": "tg", "template": "tg_intro", "action": "Написать в Telegram"},
    "email_opened": {"days": 2, "channel": "tg", "template": "tg_intro", "action": "Написать в TG (открыл письмо!)"},
    "tg_sent": {"days": 4, "channel": "wa", "template": "wa_intro", "action": "Написать в WhatsApp"},
    "wa_sent": {"days": 7, "channel": "email", "template": "follow_up_email", "action": "Финальное письмо"},
}


@router.get("/followup")
def get_followup_queue(
    db: Session = Depends(get_db),
    city: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
):
    """Очередь follow-up: контакты, которым нужно написать прямо сейчас.

    Логика:
    - stage в STAGE_NEXT_ACTION
    - нет stop_automation
    - с последнего касания прошло >= days из конфига стадии
    """
    now = datetime.now(timezone.utc)

    q = (
        db.query(CompanyRow, EnrichedCompanyRow, CrmContactRow)
        .outerjoin(EnrichedCompanyRow, CompanyRow.id == EnrichedCompanyRow.id)
        .join(CrmContactRow, CompanyRow.id == CrmContactRow.company_id)
        .filter(
            CrmContactRow.funnel_stage.in_(list(STAGE_NEXT_ACTION.keys())),
            CrmContactRow.stop_automation == 0,
        )
    )
    if city:
        q = q.filter(CompanyRow.city == city)

    rows = q.all()
    result = []

    for company, enriched, contact in rows:
        stage = contact.funnel_stage
        rule = STAGE_NEXT_ACTION.get(stage)
        if not rule:
            continue

        # Проверяем, прошло ли достаточно дней
        days_required = rule["days"]
        last = contact.last_contact_at
        if days_required > 0 and last:
            last_aware = last.replace(tzinfo=timezone.utc) if last.tzinfo is None else last
            days_since = (now - last_aware).days
            if days_since < days_required:
                continue
        # Для stage "new" (days=0): показывать только если ещё не было касаний
        if stage == "new" and last is not None:
            continue

        # Проверяем доступность канала
        messengers = enriched.messengers or {} if enriched else {}
        channel = rule["channel"]
        channel_available = True
        if channel == "tg" and not messengers.get("telegram"):
            if messengers.get("whatsapp"):
                channel = "wa"
                rule = STAGE_NEXT_ACTION.get("tg_sent", rule)
            else:
                channel_available = False
        elif channel == "wa" and not messengers.get("whatsapp"):
            channel_available = False

        result.append({
            "company_id": company.id,
            "name": company.name_best,
            "city": company.city,
            "funnel_stage": stage,
            "days_since_last_contact": (
                (now - last.replace(tzinfo=timezone.utc) if last.tzinfo is None else now - last).days
                if last else 999
            ),
            "recommended_channel": channel,
            "channel_available": channel_available,
            "template_name": rule["template"],
            "action": rule["action"],
            "telegram": messengers.get("telegram"),
            "whatsapp": messengers.get("whatsapp"),
            "emails": company.emails or [],
            "crm_score": enriched.crm_score if enriched else 0,
            "segment": enriched.segment if enriched else "D",
        })

    # Сортируем: сначала высокий приоритет (score), потом давно не писали
    result.sort(key=lambda x: (-x["crm_score"], -x["days_since_last_contact"]))
    return {"items": result[:limit], "total": len(result)}
