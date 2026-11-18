from __future__ import annotations

import hashlib
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status as http_status,
)
from fastapi.responses import Response
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import generate_signing_token
from app.models import (
    DeliveryStatus,
    Document,
    DocumentStatus,
    SignatureField,
    SigningToken,
    User,
)
from app.routers.auth import get_current_user
from app.schemas.document import DocumentResponse
from app.schemas.signature import (
    SendDocumentRequest,
    SendDocumentResponse,
    SignatureFieldResponse,
    SignatureFieldSaveRequest,
)
from app.services.audit_service import record_audit_event
from app.services.email_service import EmailBackend, EmailDeliveryError, EmailMessage, get_email_service
from app.services.pdf_service import PdfInspectionError, inspect_pdf_bytes
from app.services.storage_service import StorageBackend, get_storage_service
from app.utils.request_meta import RequestMeta, get_request_meta

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/docs", tags=["documents"])
UPLOAD_CHUNK_SIZE = 64 * 1024


async def _read_bounded_upload(file: UploadFile) -> tuple[bytes, str]:
    chunks: list[bytes] = []
    digest = hashlib.sha256()
    total_bytes = 0
    max_bytes = settings.max_upload_size_bytes

    while True:
        read_size = min(UPLOAD_CHUNK_SIZE, max_bytes - total_bytes + 1)
        chunk = await file.read(read_size)
        if not chunk:
            break
        total_bytes += len(chunk)
        if total_bytes > max_bytes:
            raise HTTPException(
                status_code=http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"PDF exceeds the {max_bytes}-byte upload limit",
            )
        chunks.append(chunk)
        digest.update(chunk)

    if total_bytes == 0:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty",
        )
    return b"".join(chunks), digest.hexdigest()


def _safe_filename(filename: str | None) -> str:
    basename = (filename or "").replace("\\", "/").rsplit("/", maxsplit=1)[-1].strip()
    basename = "".join(character for character in basename if character.isprintable())
    if basename in {"", ".", ".."}:
        return "document.pdf"
    return basename[:255]


def _get_owned_document(
    db: Session,
    document_id: uuid.UUID,
    owner_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> Document:
    query = select(Document).where(
        Document.id == document_id,
        Document.owner_id == owner_id,
    )
    if for_update:
        query = query.with_for_update()
    document = db.scalar(query)
    if document is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )
    return document


@router.post(
    "/upload",
    response_model=DocumentResponse,
    status_code=http_status.HTTP_201_CREATED,
)
async def upload_document(
    file: UploadFile = File(...),
    title: str | None = Form(default=None, max_length=255),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_storage_service),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> Document:
    pdf_bytes, streamed_sha256 = await _read_bounded_upload(file)
    original_filename = _safe_filename(file.filename)
    title_value = (title or "").strip() or Path(original_filename).stem or original_filename

    try:
        inspection = inspect_pdf_bytes(pdf_bytes)
    except PdfInspectionError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    if inspection.sha256_hex != streamed_sha256:
        raise RuntimeError("PDF upload digest did not match inspected file")

    document_id = uuid.uuid4()
    storage_key = f"originals/{document_id}.pdf"
    storage.save_bytes(storage_key, pdf_bytes, content_type="application/pdf")

    document = Document(
        id=document_id,
        owner_id=current_user.id,
        title=title_value,
        original_filename=original_filename,
        original_storage_key=storage_key,
        original_sha256=streamed_sha256,
        page_count=inspection.page_count,
        file_size_bytes=inspection.file_size_bytes,
        status=DocumentStatus.DRAFT,
    )
    try:
        db.add(document)
        record_audit_event(
            db,
            document_id=document_id,
            event_type="DOCUMENT_UPLOADED",
            actor_type="SENDER",
            actor_id=current_user.id,
            actor_email=current_user.email,
            request_meta=request_meta,
        )
        db.commit()
    except SQLAlchemyError:
        try:
            db.rollback()
        except SQLAlchemyError:
            logger.exception(
                "Could not roll back database transaction after upload failure",
                extra={"document_id": str(document_id)},
            )
        try:
            storage.delete(storage_key)
        except OSError:
            logger.exception(
                "Could not remove orphaned upload after database failure",
                extra={"document_id": str(document_id)},
            )
        raise

    db.refresh(document)
    return document


@router.get("", response_model=list[DocumentResponse])
def list_documents(
    status: DocumentStatus | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Document]:
    query = select(Document).where(Document.owner_id == current_user.id)
    if status is not None:
        query = query.where(Document.status == status)
    return list(
        db.scalars(query.order_by(Document.created_at.desc(), Document.id.desc())).all()
    )


@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Document:
    return _get_owned_document(db, document_id, current_user.id)


@router.get("/{document_id}/file")
def download_document_file(
    document_id: uuid.UUID,
    version: Literal["original", "signed"] | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_storage_service),
) -> Response:
    document = _get_owned_document(db, document_id, current_user.id)
    use_signed = version == "signed" or (
        version is None
        and document.status == DocumentStatus.SIGNED
        and document.signed_storage_key is not None
    )
    if use_signed:
        storage_key = document.signed_storage_key
        expected_sha256 = document.signed_sha256
    else:
        storage_key = document.original_storage_key
        expected_sha256 = document.original_sha256

    if storage_key is None or expected_sha256 is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Requested document version not found",
        )

    try:
        file_bytes = storage.read_bytes(storage_key)
    except (OSError, ValueError) as exc:
        logger.exception(
            "Stored document could not be read",
            extra={"document_id": str(document_id)},
        )
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stored document is unavailable",
        ) from exc

    if hashlib.sha256(file_bytes).hexdigest() != expected_sha256:
        logger.error(
            "Stored document failed SHA-256 integrity verification",
            extra={"document_id": str(document_id), "storage_key": storage_key},
        )
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stored document integrity verification failed",
        )

    return Response(
        content=file_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f"inline; filename*=UTF-8''{quote(document.original_filename, safe='')}"
            )
        },
    )


@router.put("/{document_id}/fields", response_model=list[SignatureFieldResponse])
def save_signature_fields(
    document_id: uuid.UUID,
    payload: SignatureFieldSaveRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> list[SignatureField]:
    document = _get_owned_document(
        db,
        document_id,
        current_user.id,
        for_update=True,
    )
    if document.status != DocumentStatus.DRAFT:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="Signature fields can only be changed while the document is a draft",
        )
    if any(field.page_number > document.page_count for field in payload.fields):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Signature field page number exceeds the document page count",
        )

    fields = [
        SignatureField(
            document_id=document.id,
            page_number=field.page_number,
            x_norm=field.x_norm,
            y_norm=field.y_norm,
            width_norm=field.width_norm,
            height_norm=field.height_norm,
            label=field.label,
            required=field.required,
        )
        for field in payload.fields
    ]
    try:
        db.execute(
            delete(SignatureField).where(SignatureField.document_id == document.id)
        )
        db.add_all(fields)
        record_audit_event(
            db,
            document_id=document.id,
            event_type="FIELDS_SAVED",
            actor_type="SENDER",
            actor_id=current_user.id,
            actor_email=current_user.email,
            request_meta=request_meta,
            metadata={"field_count": len(fields)},
        )
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise

    return list(
        db.scalars(
            select(SignatureField)
            .where(SignatureField.document_id == document.id)
            .order_by(
                SignatureField.page_number.asc(),
                SignatureField.y_norm.asc(),
                SignatureField.x_norm.asc(),
                SignatureField.id.asc(),
            )
        ).all()
    )


@router.get("/{document_id}/fields", response_model=list[SignatureFieldResponse])
def get_signature_fields(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SignatureField]:
    document = _get_owned_document(db, document_id, current_user.id)
    return list(
        db.scalars(
            select(SignatureField)
            .where(SignatureField.document_id == document.id)
            .order_by(
                SignatureField.page_number.asc(),
                SignatureField.y_norm.asc(),
                SignatureField.x_norm.asc(),
                SignatureField.id.asc(),
            )
        ).all()
    )


def _safe_delivery_error(
    error: EmailDeliveryError,
    *,
    raw_token: str,
    signing_url: str,
) -> str:
    message = str(error).replace(signing_url, "[redacted signing URL]")
    message = message.replace(raw_token, "[redacted]")
    message = re.sub(r"(?i)(#token=)[^\s&]+", r"\1[redacted]", message)
    return message[:500] or "Email delivery failed"


@router.post("/{document_id}/send", response_model=SendDocumentResponse)
def send_document_for_signature(
    document_id: uuid.UUID,
    payload: SendDocumentRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    email_service: EmailBackend = Depends(get_email_service),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> SendDocumentResponse:
    document = _get_owned_document(
        db,
        document_id,
        current_user.id,
        for_update=True,
    )
    if document.status not in {DocumentStatus.DRAFT, DocumentStatus.PENDING}:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="Only draft or pending documents can be sent",
        )

    has_fields = db.scalar(
        select(SignatureField.id)
        .where(SignatureField.document_id == document.id)
        .limit(1)
    )
    if has_fields is None:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="At least one signature field must be placed before sending",
        )

    now = datetime.now(timezone.utc)
    previous_tokens = db.scalars(
        select(SigningToken)
        .where(
            SigningToken.document_id == document.id,
            SigningToken.revoked_at.is_(None),
        )
        .with_for_update()
    ).all()
    for previous_token in previous_tokens:
        previous_token.revoked_at = now

    raw_token, token_hash = generate_signing_token()
    expires_at = now + timedelta(hours=settings.signing_link_expire_hours)
    signing_token = SigningToken(
        document_id=document.id,
        recipient_name=payload.recipient_name,
        recipient_email=str(payload.recipient_email),
        token_hash=token_hash,
        delivery_status=DeliveryStatus.PENDING,
        expires_at=expires_at,
    )
    signing_url = f"{settings.frontend_base_url.rstrip('/')}/sign#token={raw_token}"
    document.status = DocumentStatus.PENDING
    db.add(signing_token)
    record_audit_event(
        db,
        document_id=document.id,
        event_type="LINK_GENERATED",
        actor_type="SENDER",
        actor_id=current_user.id,
        actor_email=current_user.email,
        request_meta=request_meta,
        metadata={"recipient_email": str(payload.recipient_email)},
    )

    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise

    email_message = EmailMessage(
        to_email=str(payload.recipient_email),
        recipient_name=payload.recipient_name,
        sender_name=current_user.name,
        document_title=document.title,
        signing_url=signing_url,
        expires_at=expires_at,
    )
    try:
        email_service.send_signing_invitation(email_message)
    except EmailDeliveryError as exc:
        safe_error = _safe_delivery_error(
            exc,
            raw_token=raw_token,
            signing_url=signing_url,
        )
        signing_token.delivery_status = DeliveryStatus.FAILED
        signing_token.last_delivery_error = safe_error
        record_audit_event(
            db,
            document_id=document.id,
            event_type="EMAIL_DELIVERY_FAILED",
            actor_type="SYSTEM",
            actor_email=str(payload.recipient_email),
            request_meta=request_meta,
            metadata={
                "recipient_email": str(payload.recipient_email),
                "error": safe_error,
            },
        )
    else:
        signing_token.delivery_status = DeliveryStatus.SENT
        signing_token.last_delivery_error = None
        record_audit_event(
            db,
            document_id=document.id,
            event_type="EMAIL_SENT",
            actor_type="SYSTEM",
            actor_email=str(payload.recipient_email),
            request_meta=request_meta,
            metadata={"recipient_email": str(payload.recipient_email)},
        )

    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise

    return SendDocumentResponse(
        document_id=document.id,
        status=document.status,
        recipient_name=payload.recipient_name,
        recipient_email=payload.recipient_email,
        delivery_status=signing_token.delivery_status,
        last_delivery_error=signing_token.last_delivery_error,
        expires_at=signing_token.expires_at,
        signing_url=signing_url,
    )
