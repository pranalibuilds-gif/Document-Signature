from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.document import DocumentStatus


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    original_filename: str
    original_sha256: str
    signed_sha256: str | None
    page_count: int
    file_size_bytes: int
    status: DocumentStatus
    rejection_reason: str | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
