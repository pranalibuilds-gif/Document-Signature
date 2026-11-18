from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.core.database import Base


class SignatureField(Base):
    __tablename__ = "signature_fields"
    __table_args__ = (
        CheckConstraint("page_number >= 1", name="page_positive"),
        CheckConstraint("x_norm >= 0 AND x_norm <= 1", name="x_norm_bounds"),
        CheckConstraint("y_norm >= 0 AND y_norm <= 1", name="y_norm_bounds"),
        CheckConstraint("width_norm > 0 AND width_norm <= 1", name="width_norm_bounds"),
        CheckConstraint("height_norm > 0 AND height_norm <= 1", name="height_norm_bounds"),
        CheckConstraint("x_norm + width_norm <= 1", name="x_norm_extent"),
        CheckConstraint("y_norm + height_norm <= 1", name="y_norm_extent"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    page_number: Mapped[int] = mapped_column(nullable=False)
    x_norm: Mapped[float] = mapped_column(Float, nullable=False)
    y_norm: Mapped[float] = mapped_column(Float, nullable=False)
    width_norm: Mapped[float] = mapped_column(Float, nullable=False)
    height_norm: Mapped[float] = mapped_column(Float, nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False, default="Signature", server_default="Signature")
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    document: Mapped[Document] = relationship(back_populates="signature_fields")


from app.models.document import Document
