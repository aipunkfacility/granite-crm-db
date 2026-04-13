"""Pydantic схемы для валидации API запросов."""
from typing import Optional

from pydantic import BaseModel, Field


class CreateTouchRequest(BaseModel):
    channel: str = Field(..., pattern="^(email|tg|wa|manual)$")
    direction: str = Field("outgoing", pattern="^(outgoing|incoming)$")
    subject: str = ""
    body: str = ""
    note: str = ""


class UpdateCompanyRequest(BaseModel):
    funnel_stage: Optional[str] = Field(
        None,
        pattern="^(new|email_sent|email_opened|tg_sent|wa_sent|replied|interested|not_interested|unreachable)$",
    )
    notes: Optional[str] = None
    stop_automation: Optional[bool] = None


class CreateTaskRequest(BaseModel):
    title: str = Field("Follow-up", min_length=1)
    description: str = ""
    due_date: Optional[str] = None  # ISO format, validated in endpoint
    priority: str = Field("normal", pattern="^(low|normal|high)$")
    task_type: str = Field("follow_up", pattern="^(follow_up|send_portfolio|call|other)$")


class UpdateTaskRequest(BaseModel):
    status: Optional[str] = Field(None, pattern="^(pending|in_progress|done|cancelled)$")
    priority: Optional[str] = Field(None, pattern="^(low|normal|high)$")
    title: Optional[str] = Field(None, min_length=1)


class CreateCampaignRequest(BaseModel):
    name: str = Field("Campaign", min_length=1)
    template_name: str = Field("cold_email_1", min_length=1)
    filters: dict = Field(default_factory=dict)


class SendMessageRequest(BaseModel):
    channel: str = Field(..., pattern="^(tg|wa)$")
    template_name: Optional[str] = None
    text: Optional[str] = None
