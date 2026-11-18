from __future__ import annotations

import hashlib
from collections.abc import Generator

import fitz
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.database import Base, create_database_engine, get_db
from app.main import app
from app.services.storage_service import LocalDiskStorage, get_storage_service


@pytest.fixture
def client(
    tmp_path,
) -> Generator[TestClient, None, None]:
    engine = create_database_engine("sqlite://")
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
            yield test_client
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def create_pdf_bytes(page_count: int = 1) -> bytes:
    document = fitz.open()
    for _ in range(page_count):
        document.new_page()
    data = document.tobytes()
    document.close()
    return data


def create_encrypted_pdf_bytes() -> bytes:
    document = fitz.open()
    document.new_page()
    data = document.tobytes(
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="test-password",
        user_pw="test-password",
    )
    document.close()
    return data


def register_and_authenticate(
    client: TestClient,
    email: str = "alice@example.com",
) -> tuple[dict[str, object], dict[str, str]]:
    registration = client.post(
        "/api/auth/register",
        json={"name": "Alice", "email": email, "password": "correct horse"},
    )
    assert registration.status_code == 201
    user = registration.json()
    login = client.post(
        "/api/auth/login",
        json={"email": email, "password": "correct horse"},
    )
    assert login.status_code == 200
    return user, {"Authorization": f"Bearer {login.json()['access_token']}"}


def upload_pdf(
    client: TestClient,
    headers: dict[str, str],
    pdf_bytes: bytes,
    filename: str = "agreement.pdf",
):
    return client.post(
        "/api/docs/upload",
        headers=headers,
        files={"file": (filename, pdf_bytes, "application/pdf")},
    )


def test_upload_pdf_records_metadata_and_audit_event(client: TestClient) -> None:
    _, headers = register_and_authenticate(client)
    pdf_bytes = create_pdf_bytes(page_count=3)

    response = upload_pdf(client, headers, pdf_bytes, r"..\incoming\agreement.pdf")

    assert response.status_code == 201
    document = response.json()
    assert document["title"] == "agreement"
    assert document["original_filename"] == "agreement.pdf"
    assert document["page_count"] == 3
    assert document["file_size_bytes"] == len(pdf_bytes)
    assert document["original_sha256"] == hashlib.sha256(pdf_bytes).hexdigest()
    assert len(document["original_sha256"]) == 64
    assert document["status"] == "DRAFT"

    audit_response = client.get(
        f"/api/audit/{document['id']}",
        headers=headers,
    )
    assert audit_response.status_code == 200
    audit_events = audit_response.json()
    assert len(audit_events) == 1
    event = audit_events[0]
    assert event["event_type"] == "DOCUMENT_UPLOADED"
    assert event["actor_type"] == "SENDER"
    assert event["ip_address"]
    assert event["user_agent"]


def test_encrypted_pdf_is_rejected(client: TestClient) -> None:
    _, headers = register_and_authenticate(client)

    response = upload_pdf(client, headers, create_encrypted_pdf_bytes())

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Encrypted or password-protected PDFs are not supported"
    )


@pytest.mark.parametrize("data", [b"not a pdf file", b""])
def test_corrupt_or_empty_upload_is_rejected(client: TestClient, data: bytes) -> None:
    _, headers = register_and_authenticate(client)

    response = upload_pdf(client, headers, data)

    assert response.status_code == 400


def test_upload_rejects_file_over_configured_limit(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, headers = register_and_authenticate(client)
    monkeypatch.setattr(settings, "max_upload_size_bytes", 128)

    response = upload_pdf(client, headers, create_pdf_bytes())

    assert response.status_code == 413
    assert client.get("/api/docs", headers=headers).json() == []


def test_upload_accepts_file_at_configured_limit(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, headers = register_and_authenticate(client)
    limit = settings.max_upload_size_bytes
    pdf_bytes = create_pdf_bytes()
    pdf_bytes += b" " * (limit - len(pdf_bytes))
    monkeypatch.setattr(settings, "max_upload_size_bytes", limit)

    response = upload_pdf(client, headers, pdf_bytes)

    assert response.status_code == 201
    assert response.json()["file_size_bytes"] == limit


def test_document_file_download_matches_original_and_checks_integrity(
    client: TestClient,
    tmp_path,
) -> None:
    _, headers = register_and_authenticate(client)
    pdf_bytes = create_pdf_bytes(page_count=2)
    upload_response = upload_pdf(client, headers, pdf_bytes, 'contract".pdf')
    document = upload_response.json()

    response = client.get(f"/api/docs/{document['id']}/file", headers=headers)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert '"' not in response.headers["content-disposition"]
    assert "contract%2522.pdf" in response.headers["content-disposition"]
    assert response.content == pdf_bytes
    assert hashlib.sha256(response.content).hexdigest() == document["original_sha256"]

    stored_file = tmp_path / "originals" / f"{document['id']}.pdf"
    stored_file.write_bytes(b"tampered")
    tampered_response = client.get(
        f"/api/docs/{document['id']}/file",
        headers=headers,
    )
    assert tampered_response.status_code == 500
    assert "integrity" in tampered_response.json()["detail"]


def test_user_cannot_access_another_users_document_or_audit(
    client: TestClient,
) -> None:
    _, owner_headers = register_and_authenticate(client, "owner@example.com")
    _, other_headers = register_and_authenticate(client, "other@example.com")
    upload_response = upload_pdf(client, owner_headers, create_pdf_bytes())
    document_id = upload_response.json()["id"]

    document_response = client.get(
        f"/api/docs/{document_id}",
        headers=other_headers,
    )
    file_response = client.get(
        f"/api/docs/{document_id}/file",
        headers=other_headers,
    )
    audit_response = client.get(
        f"/api/audit/{document_id}",
        headers=other_headers,
    )

    assert document_response.status_code == 404
    assert file_response.status_code == 404
    assert audit_response.status_code == 404


def test_document_listing_is_owner_scoped_and_status_filterable(
    client: TestClient,
) -> None:
    _, owner_headers = register_and_authenticate(client, "owner@example.com")
    _, other_headers = register_and_authenticate(client, "other@example.com")
    upload_pdf(client, owner_headers, create_pdf_bytes(), "one.pdf")
    upload_pdf(client, other_headers, create_pdf_bytes(), "two.pdf")

    owner_documents = client.get("/api/docs", headers=owner_headers)
    filtered_documents = client.get(
        "/api/docs?status=PENDING",
        headers=owner_headers,
    )

    assert owner_documents.status_code == 200
    assert len(owner_documents.json()) == 1
    assert owner_documents.json()[0]["original_filename"] == "one.pdf"
    assert filtered_documents.status_code == 200
    assert filtered_documents.json() == []


@pytest.mark.parametrize(
    "key",
    [
        "../outside.pdf",
        r"..\outside.pdf",
        r"C:\outside.pdf",
        r"\\server\share\outside.pdf",
        "/outside.pdf",
    ],
)
def test_storage_key_path_traversal_is_rejected(tmp_path, key: str) -> None:
    storage = LocalDiskStorage(tmp_path)

    with pytest.raises(ValueError):
        storage.save_bytes(key, b"not allowed")

    assert not (tmp_path.parent / "outside.pdf").exists()


def test_database_failure_removes_saved_file(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _, headers = register_and_authenticate(client)

    def fail_commit(_: Session) -> None:
        raise SQLAlchemyError("simulated commit failure")

    monkeypatch.setattr(Session, "commit", fail_commit)
    response = upload_pdf(client, headers, create_pdf_bytes())

    assert response.status_code == 500
    assert list((tmp_path / "originals").glob("*.pdf")) == []
