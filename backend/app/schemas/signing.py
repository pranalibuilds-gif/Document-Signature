from __future__ import annotations

import base64
import binascii
import uuid
import warnings
from datetime import datetime
from io import BytesIO

from PIL import Image, UnidentifiedImageError
from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from app.models.document import DocumentStatus
from app.schemas.signature import SignatureFieldResponse

MAX_SIGNATURE_IMAGE_BYTES = 500 * 1024
MAX_SIGNATURE_IMAGE_PIXELS = 4_000_000
MAX_SIGNATURE_IMAGE_DIMENSION = 4096
MAX_SIGNATURE_BASE64_LENGTH = 700_000
_PNG_DATA_URI_PREFIX = "data:image/png;base64,"


def decode_signature_image(signature_image_base64: str) -> bytes:
    encoded = signature_image_base64
    if encoded.startswith("data:"):
        prefix, separator, encoded = encoded.partition(",")
        if not separator or prefix.lower() != _PNG_DATA_URI_PREFIX[:-1]:
            raise ValueError("Signature image must be a PNG data URI")

    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Signature image must contain valid base64") from exc

    if not image_bytes or len(image_bytes) > MAX_SIGNATURE_IMAGE_BYTES:
        raise ValueError("Signature PNG must be no larger than 500 KiB")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(image_bytes)) as image:
                if image.format != "PNG":
                    raise ValueError("Signature image must be a PNG")
                width, height = image.size
                if (
                    width < 1
                    or height < 1
                    or width > MAX_SIGNATURE_IMAGE_DIMENSION
                    or height > MAX_SIGNATURE_IMAGE_DIMENSION
                    or width * height > MAX_SIGNATURE_IMAGE_PIXELS
                ):
                    raise ValueError("Signature PNG dimensions are too large")
                image.verify()
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
        UnidentifiedImageError,
    ) as exc:
        raise ValueError("Signature image is not a valid PNG") from exc

    return image_bytes


class GuestDocumentResponse(BaseModel):
    title: str
    page_count: int
    recipient_name: str
    recipient_email: EmailStr
    status: DocumentStatus
    fields: list[SignatureFieldResponse]
    file_url: str


class SignatureSubmission(BaseModel):
    field_id: uuid.UUID
    signature_image_base64: str = Field(
        min_length=1,
        max_length=MAX_SIGNATURE_BASE64_LENGTH,
    )

    @field_validator("signature_image_base64")
    @classmethod
    def validate_signature_image(cls, value: str) -> str:
        decode_signature_image(value)
        return value


class CompleteSigningRequest(BaseModel):
    signatures: list[SignatureSubmission] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def ensure_unique_fields(self) -> CompleteSigningRequest:
        field_ids = [signature.field_id for signature in self.signatures]
        if len(set(field_ids)) != len(field_ids):
            raise ValueError("A signature field may only be submitted once")
        return self


class CompleteSigningResponse(BaseModel):
    document_id: uuid.UUID
    status: DocumentStatus
    signed_sha256: str
    completed_at: datetime


class RejectSigningRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class RejectSigningResponse(BaseModel):
    document_id: uuid.UUID
    status: DocumentStatus
    rejection_reason: str
    completed_at: datetime
