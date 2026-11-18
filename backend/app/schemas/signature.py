from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.models.document import DocumentStatus
from app.models.signing_token import DeliveryStatus


class SignatureFieldInput(BaseModel):
    page_number: int = Field(ge=1)
    x_norm: float = Field(ge=0.0, lt=1.0)
    y_norm: float = Field(ge=0.0, lt=1.0)
    width_norm: float = Field(gt=0.0, le=1.0)
    height_norm: float = Field(gt=0.0, le=1.0)
    label: str = Field(default="Signature", min_length=1, max_length=120)
    required: bool = True

    @field_validator("label", mode="before")
    @classmethod
    def strip_label(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def ensure_bounds_fit_page(self) -> SignatureFieldInput:
        if self.x_norm + self.width_norm > 1.0:
            raise ValueError("Signature field exceeds the page's right edge")
        if self.y_norm + self.height_norm > 1.0:
            raise ValueError("Signature field exceeds the page's bottom edge")
        return self


class SignatureFieldSaveRequest(BaseModel):
    fields: list[SignatureFieldInput] = Field(min_length=1, max_length=50)


class SignatureFieldResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    page_number: int
    x_norm: float
    y_norm: float
    width_norm: float
    height_norm: float
    label: str
    required: bool
    created_at: datetime


class SendDocumentRequest(BaseModel):
    recipient_name: str = Field(min_length=1, max_length=120)
    recipient_email: EmailStr

    @field_validator("recipient_name", mode="before")
    @classmethod
    def strip_recipient_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("recipient_email", mode="before")
    @classmethod
    def normalize_recipient_email(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value


class SendDocumentResponse(BaseModel):
    document_id: uuid.UUID
    status: DocumentStatus
    recipient_name: str
    recipient_email: EmailStr
    delivery_status: DeliveryStatus
    last_delivery_error: str | None
    expires_at: datetime
    signing_url: str
