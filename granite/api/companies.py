"""Companies API: список, карточка, обновление CRM-полей."""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
# TODO: заменить cast(String).contains() на json_extract() когда будет SQLAlchemy 2.x JSON type
from sqlalchemy import String

from granite.api.deps import get_db
from granite.api.schemas import UpdateCompanyRequest
from granite.database import (
    CompanyRow, EnrichedCompanyRow, CrmContactRow,
)

__all__ = ["router"]

router = APIRouter()


def _build_company_response(company: CompanyRow, enriched: EnrichedCompanyRow | None,
                            contact: CrmContactRow | None) -> dict:
    """Собрать полный ответ по компании."""
    messengers = enriched.messengers or {} if enriched else {}
    return {
        "id": company.id,
        "name": company.name_best,
        "phones": company.phones or [],
        "website": company.website,
        "emails": company.emails or [],
        "city": company.city,
        "messengers": messengers,
        "telegram": messengers.get("telegram"),
        "whatsapp": messengers.get("whatsapp"),
        "vk": messengers.get("vk"),
        "segment": enriched.segment if enriched else None,
        "crm_score": enriched.crm_score if enriched else 0,
        "cms": enriched.cms if enriched else None,
        "has_marquiz": enriched.has_marquiz if enriched else False,
        "is_network": enriched.is_network if enriched else False,
        "tg_trust": enriched.tg_trust if enriched else {},
        "funnel_stage": contact.funnel_stage if contact else "new",
        "email_sent_count": contact.email_sent_count if contact else 0,
        "email_opened_count": contact.email_opened_count if contact else 0,
        "tg_sent_count": contact.tg_sent_count if contact else 0,
        "wa_sent_count": contact.wa_sent_count if contact else 0,
        "last_contact_at": contact.last_contact_at.isoformat() if contact and contact.last_contact_at else None,
        "notes": contact.notes if contact else "",
        "stop_automation": bool(contact.stop_automation) if contact else False,
    }


@router.get("/companies")
def list_companies(
    db: Session = Depends(get_db),
    city: Optional[str] = None,
    segment: Optional[str] = None,
    funnel_stage: Optional[str] = None,
    has_telegram: Optional[int] = None,
    has_email: Optional[int] = None,
    min_score: Optional[int] = None,
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    order_by: str = Query("crm_score", pattern="^(crm_score|name_best|city|funnel_stage)$"),
    order_dir: str = Query("desc", pattern="^(asc|desc)$"),
):
    """Список компаний с join enriched+crm. Пагинация, фильтры, сортировка."""
    q = (
        db.query(CompanyRow, EnrichedCompanyRow, CrmContactRow)
        .outerjoin(EnrichedCompanyRow, CompanyRow.id == EnrichedCompanyRow.id)
        .outerjoin(CrmContactRow, CompanyRow.id == CrmContactRow.company_id)
    )

    if city:
        q = q.filter(CompanyRow.city == city)
    if segment:
        q = q.filter(EnrichedCompanyRow.segment == segment)
    if funnel_stage:
        q = q.filter(CrmContactRow.funnel_stage == funnel_stage)
    if has_telegram == 1:
        q = q.filter(EnrichedCompanyRow.messengers.cast(String).contains('"telegram"'))
    if has_telegram == 0:
        q = q.filter(~EnrichedCompanyRow.messengers.cast(String).contains('"telegram"'))
    if has_email == 1:
        q = q.filter(
            CompanyRow.emails.isnot(None),
            CompanyRow.emails.cast(String) != "[]",
        )
    if min_score is not None:
        q = q.filter(EnrichedCompanyRow.crm_score >= min_score)
    if search:
        q = q.filter(CompanyRow.name_best.ilike(f"%{search}%"))

    order_col = {
        "crm_score": EnrichedCompanyRow.crm_score,
        "name_best": CompanyRow.name_best,
        "city": CompanyRow.city,
        "funnel_stage": CrmContactRow.funnel_stage,
    }[order_by]
    if order_dir == "desc":
        q = q.order_by(order_col.desc().nullslast())
    else:
        q = q.order_by(order_col.asc().nullsfirst())

    total = q.count()
    rows = q.offset((page - 1) * per_page).limit(per_page).all()

    items = [_build_company_response(c, e, crm) for c, e, crm in rows]
    return {"items": items, "total": total, "page": page, "per_page": per_page}


@router.get("/companies/{company_id}")
def get_company(company_id: int, db: Session = Depends(get_db)):
    """Карточка компании."""
    company = db.get(CompanyRow, company_id)
    if not company:
        raise HTTPException(404, "Company not found")
    enriched = db.get(EnrichedCompanyRow, company_id)
    contact = db.get(CrmContactRow, company_id)
    return _build_company_response(company, enriched, contact)


@router.patch("/companies/{company_id}")
def update_company(company_id: int, data: UpdateCompanyRequest, db: Session = Depends(get_db)):
    """Обновить CRM-поля компании (funnel_stage, notes, stop_automation)."""
    contact = db.get(CrmContactRow, company_id)
    if not contact:
        contact = CrmContactRow(company_id=company_id)
        db.add(contact)

    updates = data.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(contact, key, value)
    contact.updated_at = datetime.now(timezone.utc)
    return {"ok": True}
