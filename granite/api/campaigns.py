"""Campaigns API: email-рассылки по сегментам."""
import json
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import String

from granite.api.deps import get_db
from granite.api.schemas import CreateCampaignRequest
from granite.database import (
    CompanyRow, EnrichedCompanyRow, CrmContactRow,
    CrmEmailLogRow, CrmEmailCampaignRow, CrmTemplateRow,
)
from loguru import logger

__all__ = ["router"]

router = APIRouter()


@router.post("/campaigns")
def create_campaign(data: CreateCampaignRequest, db: Session = Depends(get_db)):
    """Создать кампанию. Body: {name, template_name, filters?: {city?, segment?, min_score?}}"""
    campaign = CrmEmailCampaignRow(
        name=data.name,
        template_name=data.template_name,
        filters=json.dumps(data.filters),
    )
    db.add(campaign)
    db.flush()
    return {"ok": True, "campaign_id": campaign.id}


@router.get("/campaigns")
def list_campaigns(db: Session = Depends(get_db)):
    campaigns = db.query(CrmEmailCampaignRow).order_by(CrmEmailCampaignRow.created_at.desc()).all()
    return [
        {
            "id": c.id, "name": c.name, "template_name": c.template_name,
            "status": c.status, "total_sent": c.total_sent,
            "total_opened": c.total_opened, "total_replied": c.total_replied,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in campaigns
    ]


def _get_campaign_recipients(campaign: CrmEmailCampaignRow, db: Session) -> list:
    """Найти получателей кампании по фильтрам.

    Дедупликация:
    - По campaign_id (не отправлять дважды в одну кампанию).
    - По email-адресу (один info@granit.ru у разных компаний).
    """
    filters = json.loads(campaign.filters or "{}")

    sent_company_ids = {
        row[0] for row in
        db.query(CrmEmailLogRow.company_id)
        .filter(CrmEmailLogRow.campaign_id == campaign.id)
        .all()
    }

    q = (
        db.query(CompanyRow, EnrichedCompanyRow, CrmContactRow)
        .outerjoin(EnrichedCompanyRow, CompanyRow.id == EnrichedCompanyRow.id)
        .outerjoin(CrmContactRow, CompanyRow.id == CrmContactRow.company_id)
        .filter(
            CompanyRow.emails.isnot(None),
            CompanyRow.emails.cast(String) != "[]",
            CompanyRow.emails.cast(String) != "",
        )
    )

    if filters.get("city"):
        q = q.filter(CompanyRow.city == filters["city"])
    if filters.get("segment"):
        q = q.filter(EnrichedCompanyRow.segment == filters["segment"])
    if filters.get("min_score"):
        q = q.filter(EnrichedCompanyRow.crm_score >= filters["min_score"])

    rows = q.all()
    recipients = []
    seen_emails = set()
    for company, enriched, contact in rows:
        if company.id in sent_company_ids:
            continue
        if contact and contact.stop_automation:
            continue
        emails = company.emails or []
        if not emails:
            continue
        email_to = emails[0].lower().strip()
        if email_to in seen_emails:
            continue
        seen_emails.add(email_to)
        recipients.append((company, enriched, contact, email_to))
    return recipients


@router.get("/campaigns/{campaign_id}")
def get_campaign(campaign_id: int, db: Session = Depends(get_db)):
    """Детали кампании + предпросмотр получателей."""
    campaign = db.get(CrmEmailCampaignRow, campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    recipients = _get_campaign_recipients(campaign, db)
    return {
        "id": campaign.id, "name": campaign.name,
        "template_name": campaign.template_name,
        "status": campaign.status,
        "filters": json.loads(campaign.filters or "{}"),
        "total_sent": campaign.total_sent,
        "total_opened": campaign.total_opened,
        "preview_recipients": len(recipients),
    }


@router.post("/campaigns/{campaign_id}/run")
def run_campaign(campaign_id: int):
    """Запустить кампанию с SSE прогресс-баром.

    Возвращает Server-Sent Events: data: {"sent": N, "total": M, "current": "email"}

    Генератор открывает собственную сессию БД (не Depends),
    потому что StreamingResponse ленивый.

    Rate limiting: 3 сек между отправками.
    Batch commits: каждые 10 отправок.
    Interruption: try/finally ставит status="paused" при обрыве SSE.
    """
    def generate():
        import time as _time
        from granite.database import Database, CrmEmailLogRow, CrmEmailCampaignRow, CrmTemplateRow, CrmTouchRow
        from granite.email.sender import EmailSender

        SEND_DELAY = 3
        BATCH_COMMIT = 10
        MAX_SENDS_PER_RUN = 100

        campaign_db = Database()
        campaign = None
        try:
            with campaign_db.session_scope() as session:
                campaign = session.get(CrmEmailCampaignRow, campaign_id)
                if not campaign:
                    yield f"data: {json.dumps({'error': 'Campaign not found'})}\n\n"
                    return

                if campaign.status == "running":
                    yield f"data: {json.dumps({'error': 'Campaign already running'})}\n\n"
                    return

                template = session.query(CrmTemplateRow).filter_by(name=campaign.template_name).first()
                if not template:
                    yield f"data: {json.dumps({'error': 'Template not found'})}\n\n"
                    return

                recipients = _get_campaign_recipients(campaign, session)

                from_name = os.environ.get("FROM_NAME", "")
                sender = EmailSender()
                sent = 0
                total = len(recipients)

                if total > MAX_SENDS_PER_RUN:
                    recipients = recipients[:MAX_SENDS_PER_RUN]
                    logger.warning(f"Campaign {campaign_id}: truncated to {MAX_SENDS_PER_RUN} (total: {total})")

                campaign.status = "running"
                campaign.started_at = datetime.now(timezone.utc)
                session.commit()

                yield f"data: {json.dumps({'status': 'started', 'total': len(recipients)})}\n\n"

                for company, enriched, contact, email_to in recipients:
                    city = company.city or ""
                    render_kwargs = {
                        "from_name": from_name,
                        "city": city,
                        "company_name": company.name_best or "",
                        "website": company.website or "",
                    }
                    subject = template.render_subject(**render_kwargs)
                    body = template.render(**render_kwargs)
                    tracking_id = sender.send(
                        company_id=company.id,
                        email_to=email_to,
                        subject=subject,
                        body_text=body,
                        template_name=template.name,
                        db_session=session,
                        campaign_id=campaign.id,
                    )
                    if tracking_id:
                        sent += 1
                        campaign.total_sent = sent
                        if contact:
                            contact.funnel_stage = "email_sent"
                            contact.email_sent_count = (contact.email_sent_count or 0) + 1
                            contact.last_email_sent_at = datetime.now(timezone.utc)
                        session.add(CrmTouchRow(
                            company_id=company.id, channel="email", direction="outgoing",
                            subject=subject, body=f"[tracking_id={tracking_id}]",
                        ))
                        if sent % BATCH_COMMIT == 0:
                            session.commit()

                    yield f"data: {json.dumps({'sent': sent, 'total': len(recipients), 'current': email_to})}\n\n"
                    _time.sleep(SEND_DELAY)

                session.commit()

                campaign.status = "completed"
                campaign.completed_at = datetime.now(timezone.utc)
                session.commit()
                yield f"data: {json.dumps({'status': 'completed', 'sent': sent, 'total': len(recipients)})}\n\n"
        except GeneratorExit:
            if campaign:
                try:
                    with campaign_db.session_scope() as session:
                        camp = session.get(CrmEmailCampaignRow, campaign_id)
                        if camp and camp.status == "running":
                            camp.status = "paused"
                except Exception:
                    pass
            logger.info(f"Campaign {campaign_id}: SSE disconnected, status set to 'paused'")
        finally:
            campaign_db.engine.dispose()

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/campaigns/{campaign_id}/stats")
def campaign_stats(campaign_id: int, db: Session = Depends(get_db)):
    """Статистика кампании."""
    campaign = db.get(CrmEmailCampaignRow, campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    return {
        "id": campaign.id, "name": campaign.name, "status": campaign.status,
        "total_sent": campaign.total_sent,
        "total_opened": campaign.total_opened,
        "total_replied": campaign.total_replied,
        "open_rate": round(campaign.total_opened / campaign.total_sent * 100, 1)
                     if campaign.total_sent else 0,
    }
