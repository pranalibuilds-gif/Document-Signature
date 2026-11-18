from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class EmailDeliveryError(Exception):
    """Raised when an invitation cannot be delivered by the selected provider."""


@dataclass(frozen=True)
class EmailMessage:
    to_email: str
    recipient_name: str
    sender_name: str
    document_title: str
    signing_url: str
    expires_at: datetime


class EmailBackend(Protocol):
    def send_signing_invitation(self, message: EmailMessage) -> None: ...


class ConsoleEmailService:
    def send_signing_invitation(self, message: EmailMessage) -> None:
        logger.info(
            "Signing invitation prepared for console delivery; signing URL omitted",
            extra={
                "to_email": message.to_email,
                "recipient_name": message.recipient_name,
                "sender_name": message.sender_name,
                "document_title": message.document_title,
                "expires_at": message.expires_at.isoformat(),
            },
        )


class ResendEmailService:
    def __init__(
        self,
        *,
        api_key: str,
        from_address: str,
        timeout: float = 10.0,
    ) -> None:
        self.api_key = api_key
        self.from_address = from_address
        self.timeout = timeout

    def send_signing_invitation(self, message: EmailMessage) -> None:
        recipient_name = html.escape(message.recipient_name)
        sender_name = html.escape(message.sender_name)
        document_title = html.escape(message.document_title)
        signing_url = html.escape(message.signing_url, quote=True)
        expires_at = html.escape(message.expires_at.isoformat())
        payload = {
            "from": self.from_address,
            "to": [message.to_email],
            "subject": f"{message.sender_name} requested your signature",
            "html": (
                f"<p>Hello {recipient_name},</p>"
                f"<p>{sender_name} sent you <strong>{document_title}</strong> "
                "to review and sign.</p>"
                f'<p><a href="{signing_url}">Review and sign document</a></p>'
                f"<p>This link expires at {expires_at}.</p>"
            ),
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise EmailDeliveryError("Email provider request failed") from exc

        if not response.is_success:
            raise EmailDeliveryError(
                f"Email provider returned HTTP {response.status_code}"
            )


def get_email_service() -> EmailBackend:
    if settings.email_backend == "console":
        return ConsoleEmailService()
    if settings.email_backend == "resend":
        if (
            settings.resend_api_key is None
            or not settings.resend_api_key.get_secret_value()
        ):
            raise RuntimeError("RESEND_API_KEY is required when EMAIL_BACKEND=resend")
        return ResendEmailService(
            api_key=settings.resend_api_key.get_secret_value(),
            from_address=settings.email_from_address,
        )
    raise RuntimeError(f"Unsupported email backend: {settings.email_backend}")
