from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import AuditLog, Document, User
from app.routers.auth import get_current_user
from app.schemas.audit import AuditResponse

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/{document_id}", response_model=list[AuditResponse])
def get_document_audit(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AuditLog]:
    document = db.scalar(
        select(Document.id).where(
            Document.id == document_id,
            Document.owner_id == current_user.id,
        )
    )
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )
    return list(
        db.scalars(
            select(AuditLog)
            .where(AuditLog.document_id == document_id)
            .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
        ).all()
    )
