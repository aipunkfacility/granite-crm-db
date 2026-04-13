"""Touches API: лог касаний компании."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from granite.api.deps import get_db
from granite.api.schemas import CreateTouchRequest
from granite.api.stage_transitions import apply_outgoing_touch, apply_incoming_touch
from granite.database import CrmTouchRow, CrmContactRow

__all__ = ["router"]

router = APIRouter()


@router.post("/companies/{company_id}/touches")
def create_touch(company_id: int, data: CreateTouchRequest, db: Session = Depends(get_db)):
    """Залогировать касание.

    Body: {channel: email|tg|wa|manual, direction: outgoing|incoming, body?: str, subject?: str}
    """
    touch = CrmTouchRow(
        company_id=company_id,
        channel=data.channel,
        direction=data.direction,
        subject=data.subject,
        body=data.body,
        note=data.note,
    )
    db.add(touch)

    contact = db.get(CrmContactRow, company_id)
    if not contact:
        contact = CrmContactRow(company_id=company_id)
        db.add(contact)

    if data.direction == "outgoing":
        apply_outgoing_touch(contact, data.channel)
    elif data.direction == "incoming":
        apply_incoming_touch(contact)

    db.flush()
    return {"ok": True, "touch_id": touch.id}


@router.get("/companies/{company_id}/touches")
def get_touches(company_id: int, db: Session = Depends(get_db)):
    """История касаний компании (новые первые)."""
    touches = (
        db.query(CrmTouchRow)
        .filter_by(company_id=company_id)
        .order_by(CrmTouchRow.created_at.desc())
        .all()
    )
    return [
        {
            "id": t.id,
            "channel": t.channel,
            "direction": t.direction,
            "subject": t.subject,
            "body": t.body,
            "note": t.note,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in touches
    ]
