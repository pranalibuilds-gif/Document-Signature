from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import hash_signing_token
from app.models import Document, DocumentStatus, SignatureField, SigningToken
from app.schemas.signing import (
    CompleteSigningRequest,
    CompleteSigningResponse,
    GuestDocumentResponse,
    RejectSigningRequest,
    RejectSigningResponse,
    decode_signature_image,
)
from app.services.audit_service import record_audit_event
from app.services.pdf_service import PdfStampingError, stamp_signatures_on_pdf
from app.services.storage_service import StorageBackend, get_storage_service
from app.utils.request_meta import RequestMeta, get_request_meta

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/public/sign", tags=["public signing"])


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Signing link not found",
    )


def _get_header_signing_token(
    signing_token: str | None = Header(default=None, alias="X-Signing-Token"),
) -> str:
    if signing_token is None or not signing_token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Signing token is required",
        )
    return signing_token.strip()


@router.get("", response_model=GuestDocumentResponse)
def view_signing_document_with_header(
    raw_token: str = Depends(_get_header_signing_token),
    db: Session = Depends(get_db),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> GuestDocumentResponse:
    return view_signing_document(raw_token, db, request_meta).model_copy(
        update={"file_url": "/api/public/sign/file"}
    )


@router.get("/file")
def view_original_pdf_with_header(
    raw_token: str = Depends(_get_header_signing_token),
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_storage_service),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> Response:
    return view_original_pdf(raw_token, db, storage, request_meta)


@router.post("/complete", response_model=CompleteSigningResponse)
def complete_signing_with_header(
    payload: CompleteSigningRequest,
    raw_token: str = Depends(_get_header_signing_token),
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_storage_service),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> CompleteSigningResponse:
    return complete_signing(raw_token, payload, db, storage, request_meta)


@router.post("/reject", response_model=RejectSigningResponse)
def reject_signing_with_header(
    payload: RejectSigningRequest,
    raw_token: str = Depends(_get_header_signing_token),
    db: Session = Depends(get_db),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> RejectSigningResponse:
    return reject_signing(raw_token, payload, db, request_meta)


def _get_signing_context(
    db: Session,
    raw_token: str,
    *,
    for_update: bool = False,
) -> tuple[SigningToken, Document]:
    if for_update and db.get_bind().dialect.name == "sqlite":
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")

    token_hash = hash_signing_token(raw_token)
    token_reference = db.execute(
        select(SigningToken.id, SigningToken.document_id).where(
            SigningToken.token_hash == token_hash
        )
    ).one_or_none()
    if token_reference is None:
        raise _not_found()

    token_id, document_id = token_reference
    if for_update:
        document = db.scalar(
            select(Document)
            .where(Document.id == document_id)
            .with_for_update()
        )
        token = db.scalar(
            select(SigningToken)
            .where(
                SigningToken.id == token_id,
                SigningToken.token_hash == token_hash,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    else:
        token = db.scalar(
            select(SigningToken).where(
                SigningToken.id == token_id,
                SigningToken.token_hash == token_hash,
            )
        )
        document = db.get(Document, document_id)

    if token is None or document is None:
        raise _not_found()

    expires_at = token.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if (
        token.revoked_at is not None
        or token.used_at is not None
        or expires_at <= datetime.now(timezone.utc)
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_409_CONFLICT if for_update else status.HTTP_410_GONE
            ),
            detail=(
                "Signing link has already been used, revoked, or expired"
                if for_update
                else "Signing link has expired or is no longer active"
            ),
        )
    if document.status != DocumentStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document is no longer awaiting a signature",
        )
    return token, document


def _read_original_pdf(document: Document, storage: StorageBackend) -> bytes:
    try:
        original_pdf = storage.read_bytes(document.original_storage_key)
    except (OSError, ValueError) as exc:
        logger.exception(
            "Original PDF could not be read for guest signing",
            extra={"document_id": str(document.id)},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stored document is unavailable",
        ) from exc

    if hashlib.sha256(original_pdf).hexdigest() != document.original_sha256:
        logger.error(
            "Original PDF failed SHA-256 integrity verification",
            extra={"document_id": str(document.id)},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stored document integrity verification failed",
        )
    return original_pdf


def _cleanup_saved_signature(storage: StorageBackend, storage_key: str) -> None:
    try:
        storage.delete(storage_key)
    except FileNotFoundError:
        return
    except (OSError, ValueError) as exc:
        logger.exception(
            "Orphaned signed PDF could not be removed",
            extra={"storage_key": storage_key},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Signed PDF cleanup failed after a database error",
        ) from exc


@router.get("/{raw_token}", response_model=GuestDocumentResponse)
def view_signing_document(
    raw_token: str,
    db: Session = Depends(get_db),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> GuestDocumentResponse:
    token, document = _get_signing_context(db, raw_token)
    fields = list(
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
    record_audit_event(
        db,
        document_id=document.id,
        event_type="DOCUMENT_VIEWED",
        actor_type="GUEST",
        actor_email=token.recipient_email,
        request_meta=request_meta,
    )
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise

    return GuestDocumentResponse(
        title=document.title,
        page_count=document.page_count,
        recipient_name=token.recipient_name,
        recipient_email=token.recipient_email,
        status=document.status,
        fields=fields,
        file_url=f"/api/public/sign/{raw_token}/file",
    )


@router.get("/{raw_token}/file")
def view_original_pdf(
    raw_token: str,
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_storage_service),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> Response:
    token, document = _get_signing_context(db, raw_token)
    original_pdf = _read_original_pdf(document, storage)
    record_audit_event(
        db,
        document_id=document.id,
        event_type="DOCUMENT_VIEWED",
        actor_type="GUEST",
        actor_email=token.recipient_email,
        request_meta=request_meta,
        metadata={"content": "original_pdf"},
    )
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise

    return Response(
        content=original_pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                "inline; filename*=UTF-8''"
                f"{quote(document.original_filename, safe='')}"
            )
        },
    )


@router.post(
    "/{raw_token}/complete",
    response_model=CompleteSigningResponse,
)
def complete_signing(
    raw_token: str,
    payload: CompleteSigningRequest,
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_storage_service),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> CompleteSigningResponse:
    token, document = _get_signing_context(db, raw_token, for_update=True)
    fields = list(
        db.scalars(
            select(SignatureField)
            .where(SignatureField.document_id == document.id)
            .order_by(SignatureField.page_number.asc(), SignatureField.id.asc())
        ).all()
    )
    fields_by_id = {field.id: field for field in fields}
    signatures_by_id = {
        signature.field_id: decode_signature_image(
            signature.signature_image_base64
        )
        for signature in payload.signatures
    }
    submitted_field_ids = set(signatures_by_id)
    if submitted_field_ids - fields_by_id.keys():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A submitted signature field does not belong to this document",
        )
    required_field_ids = {
        field.id for field in fields if field.required
    }
    if required_field_ids - submitted_field_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Every required signature field must be completed",
        )

    completed_at = datetime.now(timezone.utc)
    token.used_at = completed_at
    token.revoked_at = completed_at
    db.flush()
    signed_storage_key = f"signed/{document.id}/{uuid.uuid4()}.pdf"
    saved_signed_pdf = False

    try:
        original_pdf = _read_original_pdf(document, storage)
        signed_pdf = stamp_signatures_on_pdf(
            original_pdf,
            [
                (fields_by_id[field_id], image_bytes)
                for field_id, image_bytes in signatures_by_id.items()
            ],
        )
        saved_signed_pdf = True
        storage.save_bytes(
            signed_storage_key,
            signed_pdf,
            content_type="application/pdf",
        )
        signed_sha256 = hashlib.sha256(signed_pdf).hexdigest()
        document.signed_storage_key = signed_storage_key
        document.signed_sha256 = signed_sha256
        document.status = DocumentStatus.SIGNED
        document.completed_at = completed_at
        record_audit_event(
            db,
            document_id=document.id,
            event_type="DOCUMENT_SIGNED",
            actor_type="GUEST",
            actor_email=token.recipient_email,
            request_meta=request_meta,
            metadata={
                "signed_sha256": signed_sha256,
                "signature_count": len(signatures_by_id),
            },
        )
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        if saved_signed_pdf:
            _cleanup_saved_signature(storage, signed_storage_key)
        raise
    except HTTPException:
        db.rollback()
        if saved_signed_pdf:
            _cleanup_saved_signature(storage, signed_storage_key)
        raise
    except (OSError, PdfStampingError, ValueError) as exc:
        db.rollback()
        if saved_signed_pdf:
            _cleanup_saved_signature(storage, signed_storage_key)
        logger.exception(
            "Guest signature could not be applied",
            extra={"document_id": str(document.id)},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not complete the signature",
        ) from exc

    return CompleteSigningResponse(
        document_id=document.id,
        status=document.status,
        signed_sha256=signed_sha256,
        completed_at=completed_at,
    )


@router.post(
    "/{raw_token}/reject",
    response_model=RejectSigningResponse,
)
def reject_signing(
    raw_token: str,
    payload: RejectSigningRequest,
    db: Session = Depends(get_db),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> RejectSigningResponse:
    token, document = _get_signing_context(db, raw_token, for_update=True)
    completed_at = datetime.now(timezone.utc)
    token.used_at = completed_at
    token.revoked_at = completed_at
    document.status = DocumentStatus.REJECTED
    document.rejection_reason = payload.reason
    document.completed_at = completed_at
    record_audit_event(
        db,
        document_id=document.id,
        event_type="DOCUMENT_REJECTED",
        actor_type="GUEST",
        actor_email=token.recipient_email,
        request_meta=request_meta,
        metadata={"reason": payload.reason},
    )
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise

    return RejectSigningResponse(
        document_id=document.id,
        status=document.status,
        rejection_reason=payload.reason,
        completed_at=completed_at,
    )
