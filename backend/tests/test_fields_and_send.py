from __future__ import annotations

import json
import uuid
from collections.abc import Generator
from datetime import datetime, timezone
from typing import Any

import fitz
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.database import Base, create_database_engine, get_db
from app.core.security import hash_signing_token
from app.main import app
from app.models import AuditLog, DeliveryStatus, Document, DocumentStatus, SigningToken
from app.services import email_service
from app.services.email_service import (
    EmailDeliveryError,
    EmailMessage,
    ResendEmailService,
    get_email_service,
)
from app.services.storage_service import LocalDiskStorage, get_storage_service


@pytest.fixture
def auth_context(
    tmp_path,
) -> Generator[tuple[TestClient, sessionmaker[Session]], None, None]:
    engine = create_database_engine("sqlite://")
    Base.metadata.create_all(engine)
    testing_session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db() -> Generator[Session, None, None]:
        db = testing_session_local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_storage_service] = lambda: LocalDiskStorage(tmp_path)
    try:
        with TestClient(app, raise_server_exceptions=False) as test_client:
            yield test_client, testing_session_local
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def register_and_authenticate(
    client: TestClient,
    email: str = "sender@example.com",
) -> dict[str, str]:
    registration = client.post(
        "/api/auth/register",
        json={"name": "Sender", "email": email, "password": "correct horse"},
    )
    assert registration.status_code == 201
    login = client.post(
        "/api/auth/login",
        json={"email": email, "password": "correct horse"},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def make_pdf(page_count: int = 2) -> bytes:
    document = fitz.open()
    for _ in range(page_count):
        document.new_page()
    pdf_bytes = document.tobytes()
    document.close()
    return pdf_bytes


def create_document(client: TestClient, headers: dict[str, str], page_count: int = 2) -> str:
    response = client.post(
        "/api/docs/upload",
        headers=headers,
        files={"file": ("agreement.pdf", make_pdf(page_count), "application/pdf")},
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def field_payload(
    *,
    page_number: int = 1,
    x_norm: float = 0.1,
    y_norm: float = 0.1,
    width_norm: float = 0.3,
    height_norm: float = 0.1,
    label: str = "Signature",
) -> dict[str, Any]:
    return {
        "page_number": page_number,
        "x_norm": x_norm,
        "y_norm": y_norm,
        "width_norm": width_norm,
        "height_norm": height_norm,
        "label": label,
        "required": True,
    }


def save_fields(
    client: TestClient,
    headers: dict[str, str],
    document_id: str,
    fields: list[dict[str, Any]],
):
    return client.put(
        f"/api/docs/{document_id}/fields",
        headers=headers,
        json={"fields": fields},
    )


def send_document(
    client: TestClient,
    headers: dict[str, str],
    document_id: str,
):
    return client.post(
        f"/api/docs/{document_id}/send",
        headers=headers,
        json={
            "recipient_name": "  Recipient Name ",
            "recipient_email": "RECIPIENT@example.com",
        },
    )


def test_save_and_get_multiple_fields_in_stable_page_order(
    auth_context: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = auth_context
    headers = register_and_authenticate(client)
    document_id = create_document(client, headers, page_count=2)

    response = save_fields(
        client,
        headers,
        document_id,
        [
            field_payload(page_number=2, x_norm=0.2, y_norm=0.1, label="Page two"),
            field_payload(page_number=1, x_norm=0.4, y_norm=0.5, label="Lower"),
            field_payload(page_number=1, x_norm=0.2, y_norm=0.2, label="Upper"),
        ],
    )
    get_response = client.get(
        f"/api/docs/{document_id}/fields",
        headers=headers,
    )

    assert response.status_code == 200
    assert get_response.status_code == 200
    assert [field["label"] for field in get_response.json()] == [
        "Upper",
        "Lower",
        "Page two",
    ]
    assert [field["page_number"] for field in get_response.json()] == [1, 1, 2]
    assert all(field["document_id"] == document_id for field in get_response.json())


def test_saving_fields_replaces_existing_draft_fields(
    auth_context: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, sessions = auth_context
    headers = register_and_authenticate(client)
    document_id = create_document(client, headers)
    first = save_fields(client, headers, document_id, [field_payload(label="Old")])
    assert first.status_code == 200

    replacement = save_fields(
        client,
        headers,
        document_id,
        [field_payload(x_norm=0.5, label="Replacement")],
    )
    saved = client.get(f"/api/docs/{document_id}/fields", headers=headers)

    assert replacement.status_code == 200
    assert len(saved.json()) == 1
    assert saved.json()[0]["label"] == "Replacement"
    assert saved.json()[0]["id"] != first.json()[0]["id"]
    with sessions() as db:
        events = db.scalars(
            select(AuditLog)
            .where(AuditLog.document_id == uuid.UUID(document_id))
            .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
        ).all()
    field_events = [event for event in events if event.event_type == "FIELDS_SAVED"]
    assert len(field_events) == 2
    assert field_events[-1].metadata_json == {"field_count": 1}


@pytest.mark.parametrize(
    "field",
    [
        field_payload(x_norm=-0.1),
        field_payload(x_norm=0.8, width_norm=0.3),
        field_payload(width_norm=0.0),
        field_payload(y_norm=0.8, height_norm=0.3),
    ],
)
def test_invalid_normalized_coordinates_are_rejected(
    auth_context: tuple[TestClient, sessionmaker[Session]],
    field: dict[str, Any],
) -> None:
    client, _ = auth_context
    headers = register_and_authenticate(client)
    document_id = create_document(client, headers)

    response = save_fields(client, headers, document_id, [field])

    assert response.status_code == 422


def test_signature_field_page_must_exist_in_document(
    auth_context: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = auth_context
    headers = register_and_authenticate(client)
    document_id = create_document(client, headers, page_count=2)

    response = save_fields(
        client,
        headers,
        document_id,
        [field_payload(page_number=3)],
    )

    assert response.status_code == 400


def test_fields_are_owner_scoped(
    auth_context: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = auth_context
    owner_headers = register_and_authenticate(client, "owner@example.com")
    other_headers = register_and_authenticate(client, "other@example.com")
    document_id = create_document(client, owner_headers)
    save_fields(client, owner_headers, document_id, [field_payload()])

    get_response = client.get(
        f"/api/docs/{document_id}/fields",
        headers=other_headers,
    )
    save_response = save_fields(
        client,
        other_headers,
        document_id,
        [field_payload(x_norm=0.2)],
    )

    assert get_response.status_code == 404
    assert save_response.status_code == 404


def test_send_requires_at_least_one_field(
    auth_context: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = auth_context
    headers = register_and_authenticate(client)
    document_id = create_document(client, headers)

    response = send_document(client, headers, document_id)

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "At least one signature field must be placed before sending"
    )


def test_send_stores_only_token_hash_and_returns_fragment_link(
    auth_context: tuple[TestClient, sessionmaker[Session]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, sessions = auth_context
    headers = register_and_authenticate(client)
    document_id = create_document(client, headers)
    save_fields(client, headers, document_id, [field_payload()])

    with caplog.at_level("INFO"):
        response = send_document(client, headers, document_id)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "PENDING"
    assert body["recipient_name"] == "Recipient Name"
    assert body["recipient_email"] == "recipient@example.com"
    assert body["delivery_status"] == "SENT"
    assert body["last_delivery_error"] is None
    assert body["signing_url"].startswith(
        f"{settings.frontend_base_url.rstrip('/')}/sign#token="
    )
    raw_token = body["signing_url"].split("#token=", maxsplit=1)[1]
    with sessions() as db:
        token = db.scalar(
            select(SigningToken).where(
                SigningToken.document_id == uuid.UUID(document_id)
            )
        )
        audit_logs = db.scalars(
            select(AuditLog).where(AuditLog.document_id == uuid.UUID(document_id))
        ).all()
        document = db.get(Document, uuid.UUID(document_id))

    assert token is not None
    assert token.token_hash == hash_signing_token(raw_token)
    assert token.delivery_status == DeliveryStatus.SENT
    assert raw_token not in token.token_hash
    assert raw_token not in json.dumps(
        [
            {
                "event_type": audit.event_type,
                "actor_type": audit.actor_type,
                "actor_id": audit.actor_id,
                "actor_email": audit.actor_email,
                "ip_address": audit.ip_address,
                "user_agent": audit.user_agent,
                "metadata": audit.metadata_json,
            }
            for audit in audit_logs
        ],
        default=str,
    )
    assert {audit.event_type for audit in audit_logs} >= {
        "LINK_GENERATED",
        "EMAIL_SENT",
    }
    assert all(
        raw_token not in repr(caplog_record.__dict__)
        for caplog_record in caplog.records
    )
    assert document is not None and document.status.value == "PENDING"


def test_fields_cannot_be_changed_after_sending(
    auth_context: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = auth_context
    headers = register_and_authenticate(client)
    document_id = create_document(client, headers)
    save_fields(client, headers, document_id, [field_payload()])
    sent = send_document(client, headers, document_id)

    response = save_fields(
        client,
        headers,
        document_id,
        [field_payload(x_norm=0.4)],
    )

    assert sent.status_code == 200
    assert response.status_code == 409


@pytest.mark.parametrize("terminal_status", [DocumentStatus.SIGNED, DocumentStatus.REJECTED])
def test_terminal_documents_cannot_be_sent_again(
    auth_context: tuple[TestClient, sessionmaker[Session]],
    terminal_status: DocumentStatus,
) -> None:
    client, sessions = auth_context
    headers = register_and_authenticate(client)
    document_id = create_document(client, headers)
    save_fields(client, headers, document_id, [field_payload()])
    with sessions() as db:
        document = db.get(Document, uuid.UUID(document_id))
        assert document is not None
        document.status = terminal_status
        db.commit()

    response = send_document(client, headers, document_id)

    assert response.status_code == 409


class FailingEmailService:
    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []

    def send_signing_invitation(self, message: EmailMessage) -> None:
        self.messages.append(message)
        raise EmailDeliveryError(
            f"Provider failure for {message.signing_url}"
        )


def test_email_failure_is_persisted_and_resend_revokes_previous_token(
    auth_context: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, sessions = auth_context
    headers = register_and_authenticate(client)
    document_id = create_document(client, headers)
    save_fields(client, headers, document_id, [field_payload()])
    email_service = FailingEmailService()
    app.dependency_overrides[get_email_service] = lambda: email_service

    first_response = send_document(client, headers, document_id)
    second_response = send_document(client, headers, document_id)

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    first_body = first_response.json()
    second_body = second_response.json()
    first_token = first_body["signing_url"].split("#token=", maxsplit=1)[1]
    second_token = second_body["signing_url"].split("#token=", maxsplit=1)[1]
    assert first_body["status"] == second_body["status"] == "PENDING"
    assert first_body["delivery_status"] == second_body["delivery_status"] == "FAILED"
    assert first_body["last_delivery_error"] == "Provider failure for [redacted signing URL]"
    assert first_token != second_token
    assert first_token in email_service.messages[0].signing_url

    with sessions() as db:
        tokens = db.scalars(
            select(SigningToken)
            .where(SigningToken.document_id == uuid.UUID(document_id))
        ).all()
        first_row = db.scalar(
            select(SigningToken).where(
                SigningToken.token_hash == hash_signing_token(first_token)
            )
        )
        second_row = db.scalar(
            select(SigningToken).where(
                SigningToken.token_hash == hash_signing_token(second_token)
            )
        )
        events = db.scalars(
            select(AuditLog).where(AuditLog.document_id == uuid.UUID(document_id))
        ).all()
        document = db.get(Document, uuid.UUID(document_id))

    assert len(tokens) == 2
    assert first_row is not None and first_row.revoked_at is not None
    assert second_row is not None and second_row.revoked_at is None
    assert second_row.delivery_status == DeliveryStatus.FAILED
    failure_events = [event for event in events if event.event_type == "EMAIL_DELIVERY_FAILED"]
    assert len(failure_events) == 2
    assert first_token not in json.dumps([event.metadata_json for event in events])
    assert second_token not in json.dumps([event.metadata_json for event in events])
    assert document is not None and document.status.value == "PENDING"


def test_resend_provider_failure_does_not_expose_response_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_token = "sensitive-link-token"
    captured_request: dict[str, Any] = {}

    class FakeResponse:
        is_success = False
        status_code = 503
        text = f"provider echoed {raw_token}"

    class FakeClient:
        def __init__(self, *, timeout: float) -> None:
            assert timeout > 0

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> FakeResponse:
            captured_request.update(url=url, headers=headers, payload=json)
            return FakeResponse()

    monkeypatch.setattr(email_service.httpx, "Client", FakeClient)
    service = ResendEmailService(api_key="test-key", from_address="sender@example.com")
    message = EmailMessage(
        to_email="recipient@example.com",
        recipient_name="Recipient",
        sender_name="Sender",
        document_title="Agreement",
        signing_url=f"https://example.test/sign#token={raw_token}",
        expires_at=datetime.now(timezone.utc),
    )

    with pytest.raises(EmailDeliveryError, match="HTTP 503") as error:
        service.send_signing_invitation(message)

    assert captured_request["url"] == "https://api.resend.com/emails"
    assert captured_request["headers"]["Authorization"] == "Bearer test-key"
    assert raw_token in captured_request["payload"]["html"]
    assert raw_token not in str(error.value)
