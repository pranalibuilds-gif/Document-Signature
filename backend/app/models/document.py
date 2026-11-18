from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    CheckConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.core.database import Base


class DocumentStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PENDING = "PENDING"
    SIGNED = "SIGNED"
    REJECTED = "REJECTED"


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("page_count >= 1", name="page_count_positive"),
        CheckConstraint("file_size_bytes > 0", name="file_size_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    original_storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    original_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    signed_storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    signed_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(
            DocumentStatus,
            name="document_status",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum_class: [member.value for member in enum_class],
        ),
        nullable=False,
        default=DocumentStatus.DRAFT,
        server_default=DocumentStatus.DRAFT.value,
        index=True,
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    owner: Mapped[User] = relationship(back_populates="documents")
    signature_fields: Mapped[list[SignatureField]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    signing_tokens: Mapped[list[SigningToken]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    audit_logs: Mapped[list[AuditLog]] = relationship(back_populates="document")


from app.models.audit_log import AuditLog
from app.models.signature_field import SignatureField
from app.models.signing_token import SigningToken
from app.models.user import User
