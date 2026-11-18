from __future__ import annotations

import uuid
from collections.abc import Generator
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.database import Base, create_database_engine, get_db
from app.core.security import create_access_token, verify_password
from app.main import app
from app.models import User


@pytest.fixture
def auth_context() -> Generator[tuple[TestClient, Session], None, None]:
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
    try:
        with TestClient(app) as test_client, testing_session_local() as session:
            yield test_client, session
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def register_user(client: TestClient, email: str = "alice@example.com") -> dict[str, object]:
    response = client.post(
        "/api/auth/register",
        json={"name": "  Alice Example ", "email": email, "password": "correct horse"},
    )
    assert response.status_code == 201
    return response.json()


def test_register_normalizes_email_and_never_returns_or_stores_plaintext(
    auth_context: tuple[TestClient, Session],
) -> None:
    client, db = auth_context

    response = client.post(
        "/api/auth/register",
        json={
            "name": "  Alice Example ",
            "email": " Alice@Example.com ",
            "password": "correct horse",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Alice Example"
    assert body["email"] == "alice@example.com"
    assert "password_hash" not in body
    user = db.get(User, uuid.UUID(body["id"]))
    assert user is not None
    assert user.password_hash != "correct horse"
    assert verify_password("correct horse", user.password_hash)


def test_register_rejects_case_insensitive_duplicate_email(
    auth_context: tuple[TestClient, Session],
) -> None:
    client, _ = auth_context
    register_user(client, "USER@example.com")

    response = client.post(
        "/api/auth/register",
        json={"name": "Other User", "email": "user@example.com", "password": "another pass"},
    )

    assert response.status_code == 409


def test_login_returns_access_token_with_required_claims(
    auth_context: tuple[TestClient, Session],
) -> None:
    client, _ = auth_context
    register_user(client)

    response = client.post(
        "/api/auth/login",
        json={"email": "ALICE@example.com", "password": "correct horse"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == settings.access_token_expire_minutes * 60
    claims = jwt.decode(
        body["access_token"],
        settings.jwt_secret_key.get_secret_value(),
        algorithms=[settings.jwt_algorithm],
    )
    assert claims["sub"] == body["user"]["id"]
    assert claims["type"] == "access"
    assert isinstance(claims["iat"], int)
    assert claims["exp"] > claims["iat"]
    assert "password_hash" not in body["user"]


@pytest.mark.parametrize(
    ("email", "password"),
    [
        ("alice@example.com", "wrong password"),
        ("missing@example.com", "correct horse"),
    ],
)
def test_login_returns_same_unauthorized_response_for_invalid_credentials(
    auth_context: tuple[TestClient, Session],
    email: str,
    password: str,
) -> None:
    client, _ = auth_context
    register_user(client)

    response = client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"
    assert response.headers["www-authenticate"] == "Bearer"


def test_register_rejects_password_over_bcrypt_byte_limit(
    auth_context: tuple[TestClient, Session],
) -> None:
    client, _ = auth_context

    response = client.post(
        "/api/auth/register",
        json={
            "name": "Alice",
            "email": "alice@example.com",
            "password": "é" * 37,
        },
    )

    assert response.status_code == 422


def test_me_accepts_valid_token_and_rejects_invalid_tokens(
    auth_context: tuple[TestClient, Session],
) -> None:
    client, _ = auth_context
    user = register_user(client)
    user_id = uuid.UUID(str(user["id"]))

    valid_token = create_access_token(user_id)
    response = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {valid_token}"},
    )
    assert response.status_code == 200
    assert response.json()["id"] == str(user_id)
    assert "password_hash" not in response.json()

    expired_token = create_access_token(user_id, timedelta(seconds=-1))
    wrong_secret_token = jwt.encode(
        {
            "sub": str(user_id),
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            "type": "access",
        },
        "wrong-signing-secret-that-is-long-enough",
        algorithm=settings.jwt_algorithm,
    )
    wrong_type_token = jwt.encode(
        {
            "sub": str(user_id),
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            "type": "refresh",
        },
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    invalid_subject_token = jwt.encode(
        {
            "sub": "not-a-uuid",
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            "type": "access",
        },
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    nonexistent_user_token = create_access_token(uuid.uuid4())
    invalid_tokens = [
        None,
        "not-a-jwt",
        expired_token,
        wrong_secret_token,
        wrong_type_token,
        invalid_subject_token,
        nonexistent_user_token,
    ]

    for token in invalid_tokens:
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        unauthorized = client.get("/api/auth/me", headers=headers)
        assert unauthorized.status_code == 401
        assert unauthorized.headers["www-authenticate"] == "Bearer"
