"""Funnel API: распределение контактов по стадиям воронки."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from granite.api.deps import get_db
from granite.database import CrmContactRow

__all__ = ["router"]

router = APIRouter()

FUNNEL_ORDER = [
    "new", "email_sent", "email_opened", "tg_sent", "wa_sent",
    "replied", "interested", "not_interested", "unreachable",
]


@router.get("/funnel")
def get_funnel(db: Session = Depends(get_db)):
    """Количество контактов по каждой стадии воронки."""
    rows = (
        db.query(CrmContactRow.funnel_stage, func.count())
        .group_by(CrmContactRow.funnel_stage)
        .all()
    )
    counts = {stage: cnt for stage, cnt in rows}
    return {stage: counts.get(stage, 0) for stage in FUNNEL_ORDER}
