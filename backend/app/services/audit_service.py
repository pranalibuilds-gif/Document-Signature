from __future__ import annotations

import uuid
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.models import AuditLog
from app.utils.request_meta import RequestMeta


def record_audit_event(
    db: Session,
    *,
    document_id: uuid.UUID,
    event_type: str,
    actor_type: Literal["SENDER", "GUEST", "SYSTEM"],
    actor_id: uuid.UUID | None = None,
    actor_email: str | None = None,
    request_meta: RequestMeta | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    audit_log = AuditLog(
        document_id=document_id,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        actor_email=actor_email,
        ip_address=request_meta.ip_address if request_meta else None,
        user_agent=request_meta.user_agent if request_meta else None,
        metadata_json=metadata,
    )
    db.add(audit_log)
    return audit_log
