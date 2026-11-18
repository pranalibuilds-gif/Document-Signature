from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class AuditResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    actor_type: Literal["SENDER", "GUEST", "SYSTEM"]
    actor_id: uuid.UUID | None
    actor_email: str | None
    event_type: str
    ip_address: str | None
    user_agent: str | None
    metadata_json: dict[str, Any] | None
    created_at: datetime
