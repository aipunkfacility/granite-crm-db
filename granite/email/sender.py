"""Email sender через SMTP с автоматическим tracking pixel.

Использование:
    from granite.email.sender import EmailSender
    sender = EmailSender()
    tracking_id = sender.send(company_id=42, email_to="info@company.ru",
                              template_name="cold_email_1")
"""
import os
import secrets
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

__all__ = ["EmailSender"]

# 1x1 прозрачный PNG (43 байта)
TRANSPARENT_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00"
    b"\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc"
    b"\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _is_temporary_smtp_error(e: BaseException) -> bool:
    """Retry только на временные 4xx SMTP ошибки (таймаут, rate limit)."""
    return isinstance(e, smtplib.SMTPTemporaryError)


class EmailSender:
    """Отправка писем через SMTP с tracking pixel и автоматическим retry."""

    def __init__(self):
        self.smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(os.environ.get("SMTP_PORT", "587"))
        self.smtp_user = os.environ.get("SMTP_USER", "")
        self.smtp_pass = os.environ.get("SMTP_PASS", "")
        self.base_url = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
        self.from_name = os.environ.get("FROM_NAME", "")
        self.from_addr = f"{self.from_name} <{self.smtp_user}>" if self.from_name else self.smtp_user

    def send(
        self,
        company_id: int,
        email_to: str,
        subject: str,
        body_text: str,
        template_name: str = "",
        db_session=None,
        campaign_id: int | None = None,
    ) -> str | None:
        """Отправить письмо и создать запись в crm_email_logs.

        Retry: 3 попытки с exponential backoff (2-30 сек) на SMTPTemporaryError.
        Permanent ошибки (5xx, auth failure) — не retry'ся.

        Args:
            company_id: ID компании.
            email_to: адрес получателя.
            subject: тема письма.
            body_text: текст письма (plain text).
            template_name: имя шаблона (для логирования).
            db_session: открытая сессия БД. Если передана — лог пишется в неё.
                        Если None — лог НЕ пишется.
            campaign_id: ID кампании (если письмо из кампании).

        Returns:
            tracking_id (UUID) или None при ошибке.
        """
        if not email_to or "@" not in email_to:
            logger.warning(f"EmailSender: пустой или невалидный email: {email_to!r}")
            return None

        tracking_id = secrets.token_urlsafe(16)
        pixel_url = f"{self.base_url}/api/v1/track/open/{tracking_id}.png"

        body_html = (
            f"<pre style='font-family:sans-serif;white-space:pre-wrap'>{body_text}</pre>"
            f'<img src="{pixel_url}" width="1" height="1" style="display:none" alt="">'
        )

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.from_addr
        msg["To"] = email_to
        msg.attach(MIMEText(body_text, "plain", "utf-8"))
        msg.attach(MIMEText(body_html, "html", "utf-8"))

        try:
            self._smtp_send(email_to, msg)
            logger.info(f"Email sent -> {email_to} (tracking={tracking_id})")
            if db_session is not None:
                self._log_to_db(db_session, company_id, email_to, subject,
                                template_name, tracking_id, campaign_id=campaign_id)
            return tracking_id
        except smtplib.SMTPPermanentError as e:
            logger.error(f"Email PERMANENT FAILURE -> {email_to}: {e}")
            if db_session is not None:
                self._log_to_db(db_session, company_id, email_to, subject,
                                template_name, tracking_id, error=str(e), campaign_id=campaign_id)
            return None
        except Exception as e:
            logger.error(f"Email FAILED after retries -> {email_to}: {e}")
            if db_session is not None:
                self._log_to_db(db_session, company_id, email_to, subject,
                                template_name, tracking_id, error=str(e), campaign_id=campaign_id)
            return None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=2, max=30),
        retry=retry_if_exception(_is_temporary_smtp_error),
        reraise=True,
    )
    def _smtp_send(self, email_to: str, msg: MIMEMultipart) -> None:
        """SMTP-отправка с retry на временные ошибки."""
        with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(self.smtp_user, self.smtp_pass)
            server.sendmail(self.smtp_user, [email_to], msg.as_bytes())

    def _log_to_db(
        self,
        session,
        company_id: int,
        email_to: str,
        subject: str,
        template_name: str,
        tracking_id: str,
        error: str = "",
        campaign_id: int | None = None,
    ) -> None:
        """Записать отправку в crm_email_logs."""
        from granite.database import CrmEmailLogRow
        session.add(CrmEmailLogRow(
            company_id=company_id,
            email_to=email_to,
            email_subject=subject,
            template_name=template_name,
            campaign_id=campaign_id,
            tracking_id=tracking_id,
            status="sent" if not error else "failed",
            sent_at=datetime.now(timezone.utc) if not error else None,
            error_message=error,
        ))
