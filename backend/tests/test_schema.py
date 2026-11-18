import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import create_database_engine
from sqlalchemy import DateTime

from app.models import Base, Document, SignatureField, User


def test_core_tables_are_registered_for_migrations() -> None:
    assert set(Base.metadata.tables) == {
        "users",
        "documents",
        "signature_fields",
        "signing_tokens",
        "audit_logs",
    }


def test_timestamps_are_timezone_aware() -> None:
    timestamp_columns = [
        column
        for table in Base.metadata.tables.values()
        for column in table.columns
        if column.name.endswith("_at")
    ]

    assert timestamp_columns
    assert all(
        isinstance(column.type, DateTime) and column.type.timezone
        for column in timestamp_columns
    )


def test_signature_field_coordinates_have_database_bounds() -> None:
    fields = Base.metadata.tables["signature_fields"]
    constraint_names = {constraint.name for constraint in fields.constraints}

    assert {
        "ck_signature_fields_x_norm_bounds",
        "ck_signature_fields_y_norm_bounds",
        "ck_signature_fields_width_norm_bounds",
        "ck_signature_fields_height_norm_bounds",
        "ck_signature_fields_x_norm_extent",
        "ck_signature_fields_y_norm_extent",
    }.issubset(constraint_names)


def test_sqlite_enforces_foreign_keys_and_document_field_cascade() -> None:
    engine = create_database_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        user = User(name="Alice", email="alice@example.com", password_hash="hash")
        document = Document(
            owner=user,
            title="Agreement",
            original_filename="agreement.pdf",
            original_storage_key="original.pdf",
            original_sha256="a" * 64,
            page_count=1,
            file_size_bytes=100,
        )
        field = SignatureField(
            document=document,
            page_number=1,
            x_norm=0.1,
            y_norm=0.1,
            width_norm=0.3,
            height_norm=0.1,
        )
        session.add_all([user, document, field])
        session.commit()
        document_id = document.id
        field_id = field.id

        session.execute(
            Document.__table__.delete().where(Document.id == document_id)
        )
        session.commit()
        assert session.get(SignatureField, field_id) is None

    engine.dispose()


def test_sqlite_restricts_user_deletion_when_documents_exist() -> None:
    engine = create_database_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        user = User(name="Alice", email="alice@example.com", password_hash="hash")
        session.add(user)
        session.flush()
        document = Document(
            owner_id=user.id,
            title="Agreement",
            original_filename="agreement.pdf",
            original_storage_key="original.pdf",
            original_sha256="a" * 64,
            page_count=1,
            file_size_bytes=100,
        )
        session.add(document)
        session.commit()
        user_id = user.id

        with pytest.raises(IntegrityError):
            session.execute(User.__table__.delete().where(User.id == user_id))
            session.commit()
        session.rollback()

    engine.dispose()
