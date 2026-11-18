from __future__ import annotations

import base64
import hashlib
import threading
import uuid
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import BytesIO

import fitz
from PIL import Image
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import Base, create_database_engine, get_db
from app.core.security import hash_signing_token
from app.main import app
from app.models import (
    AuditLog,
    Document,
    DocumentStatus,
    SignatureField,
    SigningToken,
)
from app.routers import signing as signing_router
from app.services.storage_service import LocalDiskStorage, get_storage_service


@pytest.fixture
def signing_context(
    tmp_path,
) -> Generator[tuple[TestClient, sessionmaker[Session], LocalDiskStorage], None, None]:
    database_url = URL.create(
        "sqlite",
        database=str(tmp_path / "signing.sqlite"),
    ).render_as_string(hide_password=False)
    engine = create_database_engine(database_url)
    Base.metadata.create_all(engine)
    testing_session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db() -> Generator[Session, None, None]:
        db = testing_session_local()
        try:
            yield db
        finally:
            db.close()

    storage = LocalDiskStorage(tmp_path)
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_storage_service] = lambda: storage
    try:
        with TestClient(app, raise_server_exceptions=False) as test_client:
            yield test_client, testing_session_local, storage
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _make_pdf(*, rotation: int = 0, cropbox: bool = False) -> bytes:
    document = fitz.open()
    page = document.new_page(width=800, height=1000)
    if cropbox:
        page.set_cropbox(fitz.Rect(80, 100, 700, 880))
    page.set_rotation(rotation)
    pdf_bytes = document.tobytes()
    document.close()
    return pdf_bytes


def _make_png_data_uri() -> str:
    image = Image.new("RGBA", (80, 40), (0, 0, 0, 0))
    output = BytesIO()
    image.save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def _prepare_signing_link(
    client: TestClient,
    *,
    rotation: int = 0,
) -> tuple[str, str, str]:
    registration = client.post(
        "/api/auth/register",
        json={
            "name": "Sender",
            "email": "sender@example.com",
            "password": "correct horse",
        },
    )
    assert registration.status_code == 201
    login = client.post(
        "/api/auth/login",
        json={"email": "sender@example.com", "password": "correct horse"},
    )
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    upload = client.post(
        "/api/docs/upload",
        headers=headers,
        files={
            "file": (
                "agreement.pdf",
                _make_pdf(rotation=rotation, cropbox=bool(rotation)),
                "application/pdf",
            )
        },
    )
    assert upload.status_code == 201
    document_id = str(upload.json()["id"])
    fields = client.put(
        f"/api/docs/{document_id}/fields",
        headers=headers,
        json={
            "fields": [
                {
                    "page_number": 1,
                    "x_norm": 0.2,
                    "y_norm": 0.25,
                    "width_norm": 0.3,
                    "height_norm": 0.2,
                    "label": "Signature",
                    "required": True,
                }
            ]
        },
    )
    assert fields.status_code == 200
    field_id = str(fields.json()[0]["id"])

    dispatch = client.post(
        f"/api/docs/{document_id}/send",
        headers=headers,
        json={
            "recipient_name": "Guest Signer",
            "recipient_email": "guest@example.com",
        },
    )
    assert dispatch.status_code == 200
    signing_url = dispatch.json()["signing_url"]
    raw_token = signing_url.split("#token=", maxsplit=1)[1]
    return document_id, field_id, raw_token


def test_guest_can_view_document_without_consuming_link(
    signing_context: tuple[TestClient, sessionmaker[Session], LocalDiskStorage],
) -> None:
    client, sessions, _ = signing_context
    document_id, field_id, raw_token = _prepare_signing_link(client)

    first_view = client.get(
        f"/api/public/sign/{raw_token}",
        headers={"user-agent": "guest-reviewer"},
    )
    second_view = client.get(f"/api/public/sign/{raw_token}")

    assert first_view.status_code == second_view.status_code == 200
    assert first_view.json()["title"] == "agreement"
    assert first_view.json()["recipient_email"] == "guest@example.com"
    assert first_view.json()["fields"][0]["id"] == field_id
    assert first_view.json()["file_url"] == f"/api/public/sign/{raw_token}/file"
    with sessions() as db:
        token = db.scalar(
            select(SigningToken).where(
                SigningToken.token_hash == hash_signing_token(raw_token)
            )
        )
        events = db.scalars(
            select(AuditLog)
            .where(AuditLog.document_id == uuid.UUID(document_id))
            .where(AuditLog.event_type == "DOCUMENT_VIEWED")
        ).all()

    assert token is not None
    assert token.used_at is None and token.revoked_at is None
    assert len(events) == 2
    assert events[0].actor_type == "GUEST"
    assert events[0].user_agent == "guest-reviewer"


def test_guest_header_routes_view_and_complete_without_token_in_url(
    signing_context: tuple[TestClient, sessionmaker[Session], LocalDiskStorage],
) -> None:
    client, sessions, _ = signing_context
    document_id, field_id, raw_token = _prepare_signing_link(client)
    headers = {"X-Signing-Token": raw_token}

    view_response = client.get("/api/public/sign", headers=headers)
    file_response = client.get("/api/public/sign/file", headers=headers)
    complete_response = client.post(
        "/api/public/sign/complete",
        headers=headers,
        json={
            "signatures": [
                {
                    "field_id": field_id,
                    "signature_image_base64": _make_png_data_uri(),
                }
            ]
        },
    )

    assert view_response.status_code == 200
    assert view_response.json()["title"] == "agreement"
    assert view_response.json()["file_url"] == "/api/public/sign/file"
    assert file_response.status_code == 200
    assert file_response.content.startswith(b"%PDF-")
    assert complete_response.status_code == 200
    assert complete_response.json()["status"] == "SIGNED"
    with sessions() as db:
        document = db.get(Document, uuid.UUID(document_id))
    assert document is not None
    assert document.status == DocumentStatus.SIGNED


def test_guest_header_route_requires_signing_token(
    signing_context: tuple[TestClient, sessionmaker[Session], LocalDiskStorage],
) -> None:
    client, _, _ = signing_context

    response = client.get("/api/public/sign")

    assert response.status_code == 401


def test_guest_header_route_can_reject_document(
    signing_context: tuple[TestClient, sessionmaker[Session], LocalDiskStorage],
) -> None:
    client, sessions, _ = signing_context
    document_id, _, raw_token = _prepare_signing_link(client)

    response = client.post(
        "/api/public/sign/reject",
        headers={"X-Signing-Token": raw_token},
        json={"reason": "Not ready to sign"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "REJECTED"
    with sessions() as db:
        document = db.get(Document, uuid.UUID(document_id))
    assert document is not None
    assert document.status == DocumentStatus.REJECTED


@pytest.mark.parametrize("invalid_state", ["expired", "revoked", "used"])
def test_guest_cannot_view_inactive_link(
    signing_context: tuple[TestClient, sessionmaker[Session], LocalDiskStorage],
    invalid_state: str,
) -> None:
    client, sessions, _ = signing_context
    _, _, raw_token = _prepare_signing_link(client)
    with sessions() as db:
        token = db.scalar(
            select(SigningToken).where(
                SigningToken.token_hash == hash_signing_token(raw_token)
            )
        )
        assert token is not None
        if invalid_state == "expired":
            token.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        elif invalid_state == "revoked":
            token.revoked_at = datetime.now(timezone.utc)
        else:
            token.used_at = datetime.now(timezone.utc)
        db.commit()

    response = client.get(f"/api/public/sign/{raw_token}")

    assert response.status_code == 410


def test_guest_file_endpoint_returns_original_pdf(
    signing_context: tuple[TestClient, sessionmaker[Session], LocalDiskStorage],
) -> None:
    client, _, _ = signing_context
    _, _, raw_token = _prepare_signing_link(client)

    response = client.get(f"/api/public/sign/{raw_token}/file")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-")


def test_guest_completion_stamps_and_persists_signed_pdf(
    signing_context: tuple[TestClient, sessionmaker[Session], LocalDiskStorage],
) -> None:
    client, sessions, storage = signing_context
    document_id, field_id, raw_token = _prepare_signing_link(client, rotation=90)
    signature_image = _make_png_data_uri()

    response = client.post(
        f"/api/public/sign/{raw_token}/complete",
        headers={"user-agent": "guest-signer"},
        json={
            "signatures": [
                {
                    "field_id": field_id,
                    "signature_image_base64": signature_image,
                }
            ]
        },
    )

    assert response.status_code == 200
    response_body = response.json()
    assert response_body["status"] == "SIGNED"
    with sessions() as db:
        document = db.get(Document, uuid.UUID(document_id))
        token = db.scalar(
            select(SigningToken).where(
                SigningToken.token_hash == hash_signing_token(raw_token)
            )
        )
        events = db.scalars(
            select(AuditLog)
            .where(AuditLog.document_id == uuid.UUID(document_id))
            .where(AuditLog.event_type == "DOCUMENT_SIGNED")
        ).all()

    assert document is not None
    assert document.status == DocumentStatus.SIGNED
    assert document.signed_storage_key is not None
    assert document.signed_sha256 == response_body["signed_sha256"]
    signed_pdf = storage.read_bytes(document.signed_storage_key)
    assert hashlib.sha256(signed_pdf).hexdigest() == document.signed_sha256
    with fitz.open(stream=signed_pdf, filetype="pdf") as signed_document:
        assert signed_document[0].get_images()
    assert token is not None
    assert token.used_at is not None and token.revoked_at is not None
    assert len(events) == 1
    assert events[0].actor_type == "GUEST"
    assert events[0].user_agent == "guest-signer"


def test_guest_rejection_persists_reason_and_revokes_link(
    signing_context: tuple[TestClient, sessionmaker[Session], LocalDiskStorage],
) -> None:
    client, sessions, _ = signing_context
    document_id, _, raw_token = _prepare_signing_link(client)

    response = client.post(
        f"/api/public/sign/{raw_token}/reject",
        headers={"user-agent": "guest-reviewer"},
        json={"reason": "  Incorrect terms  "},
    )

    assert response.status_code == 200
    assert response.json()["rejection_reason"] == "Incorrect terms"
    with sessions() as db:
        document = db.get(Document, uuid.UUID(document_id))
        token = db.scalar(
            select(SigningToken).where(
                SigningToken.token_hash == hash_signing_token(raw_token)
            )
        )
        event = db.scalar(
            select(AuditLog)
            .where(AuditLog.document_id == uuid.UUID(document_id))
            .where(AuditLog.event_type == "DOCUMENT_REJECTED")
        )

    assert document is not None
    assert document.status == DocumentStatus.REJECTED
    assert document.rejection_reason == "Incorrect terms"
    assert token is not None and token.revoked_at is not None
    assert token.used_at is not None
    assert event is not None and event.user_agent == "guest-reviewer"


def test_signing_link_cannot_be_submitted_twice(
    signing_context: tuple[TestClient, sessionmaker[Session], LocalDiskStorage],
) -> None:
    client, _, _ = signing_context
    _, field_id, raw_token = _prepare_signing_link(client)
    payload = {
        "signatures": [
            {
                "field_id": field_id,
                "signature_image_base64": _make_png_data_uri(),
            }
        ]
    }

    first_response = client.post(
        f"/api/public/sign/{raw_token}/complete",
        json=payload,
    )
    second_response = client.post(
        f"/api/public/sign/{raw_token}/complete",
        json=payload,
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 409


def test_concurrent_completion_is_serialized(
    signing_context: tuple[TestClient, sessionmaker[Session], LocalDiskStorage],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, _ = signing_context
    _, field_id, raw_token = _prepare_signing_link(client)
    stamp_started = threading.Event()
    release_stamp = threading.Event()
    second_request_started = threading.Event()
    call_lock = threading.Lock()
    context_calls = 0
    original_stamp = signing_router.stamp_signatures_on_pdf
    original_get_context = signing_router._get_signing_context

    def wait_during_first_stamp(
        original_pdf: bytes,
        fields_and_signatures: list[tuple[SignatureField, bytes]],
    ) -> bytes:
        if not stamp_started.is_set():
            stamp_started.set()
            assert release_stamp.wait(timeout=10)
        return original_stamp(original_pdf, fields_and_signatures)

    def observe_second_context_call(
        db: Session,
        token: str,
        *,
        for_update: bool = False,
    ) -> tuple[SigningToken, Document]:
        nonlocal context_calls
        if for_update:
            with call_lock:
                context_calls += 1
                if context_calls == 2:
                    second_request_started.set()
        return original_get_context(db, token, for_update=for_update)

    monkeypatch.setattr(
        signing_router,
        "stamp_signatures_on_pdf",
        wait_during_first_stamp,
    )
    monkeypatch.setattr(
        signing_router,
        "_get_signing_context",
        observe_second_context_call,
    )
    payload = {
        "signatures": [
            {
                "field_id": field_id,
                "signature_image_base64": _make_png_data_uri(),
            }
        ]
    }

    with TestClient(app, raise_server_exceptions=False) as second_client:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(
                client.post,
                f"/api/public/sign/{raw_token}/complete",
                json=payload,
            )
            assert stamp_started.wait(timeout=5)
            second = executor.submit(
                second_client.post,
                f"/api/public/sign/{raw_token}/complete",
                json=payload,
            )
            try:
                assert second_request_started.wait(timeout=5)
            finally:
                release_stamp.set()
            first_response = first.result(timeout=15)
            second_response = second.result(timeout=15)

    assert first_response.status_code == 200
    assert second_response.status_code == 409


def test_completion_requires_every_required_field(
    signing_context: tuple[TestClient, sessionmaker[Session], LocalDiskStorage],
) -> None:
    client, sessions, _ = signing_context
    document_id, field_id, raw_token = _prepare_signing_link(client)
    with sessions() as db:
        db.add(
            SignatureField(
                document_id=uuid.UUID(document_id),
                page_number=1,
                x_norm=0.1,
                y_norm=0.1,
                width_norm=0.2,
                height_norm=0.1,
                label="Second required field",
                required=True,
            )
        )
        db.commit()
    response = client.post(
        f"/api/public/sign/{raw_token}/complete",
        json={
            "signatures": [
                {
                    "field_id": field_id,
                    "signature_image_base64": _make_png_data_uri(),
                }
            ]
        },
    )

    assert response.status_code == 422


def test_signature_image_must_be_a_valid_png(
    signing_context: tuple[TestClient, sessionmaker[Session], LocalDiskStorage],
) -> None:
    client, _, _ = signing_context
    _, field_id, raw_token = _prepare_signing_link(client)

    response = client.post(
        f"/api/public/sign/{raw_token}/complete",
        json={
            "signatures": [
                {
                    "field_id": field_id,
                    "signature_image_base64": base64.b64encode(
                        b"not a PNG"
                    ).decode("ascii"),
                }
            ]
        },
    )

    assert response.status_code == 422


def test_signature_image_is_limited_to_500_kib() -> None:
    oversized_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * (500 * 1024)
    encoded = base64.b64encode(oversized_png).decode("ascii")

    from app.schemas.signing import SignatureSubmission

    with pytest.raises(ValidationError):
        SignatureSubmission(
            field_id=uuid.uuid4(),
            signature_image_base64=encoded,
        )
