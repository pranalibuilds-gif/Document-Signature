"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-18
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("original_storage_key", sa.String(length=512), nullable=False),
        sa.Column("original_sha256", sa.String(length=64), nullable=False),
        sa.Column("signed_storage_key", sa.String(length=512), nullable=True),
        sa.Column("signed_sha256", sa.String(length=64), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "DRAFT",
                "PENDING",
                "SIGNED",
                "REJECTED",
                name="document_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="DRAFT",
            nullable=False,
        ),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("page_count >= 1", name="page_count_positive"),
        sa.CheckConstraint("file_size_bytes > 0", name="file_size_positive"),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name="fk_documents_owner_id_users",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
    )
    op.create_index("ix_documents_owner_id", "documents", ["owner_id"])
    op.create_index("ix_documents_status", "documents", ["status"])

    op.create_table(
        "signature_fields",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("document_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("x_norm", sa.Float(), nullable=False),
        sa.Column("y_norm", sa.Float(), nullable=False),
        sa.Column("width_norm", sa.Float(), nullable=False),
        sa.Column("height_norm", sa.Float(), nullable=False),
        sa.Column("label", sa.String(length=120), server_default="Signature", nullable=False),
        sa.Column("required", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("page_number >= 1", name="page_positive"),
        sa.CheckConstraint(
            "x_norm >= 0 AND x_norm <= 1",
            name="x_norm_bounds",
        ),
        sa.CheckConstraint(
            "y_norm >= 0 AND y_norm <= 1",
            name="y_norm_bounds",
        ),
        sa.CheckConstraint(
            "width_norm > 0 AND width_norm <= 1",
            name="width_norm_bounds",
        ),
        sa.CheckConstraint(
            "height_norm > 0 AND height_norm <= 1",
            name="height_norm_bounds",
        ),
        sa.CheckConstraint(
            "x_norm + width_norm <= 1",
            name="x_norm_extent",
        ),
        sa.CheckConstraint(
            "y_norm + height_norm <= 1",
            name="y_norm_extent",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            ondelete="CASCADE",
            name="fk_signature_fields_document_id_documents",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_signature_fields"),
    )
    op.create_index(
        "ix_signature_fields_document_id", "signature_fields", ["document_id"]
    )

    op.create_table(
        "signing_tokens",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("document_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("recipient_name", sa.String(length=255), nullable=False),
        sa.Column("recipient_email", sa.String(length=320), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "delivery_status",
            sa.Enum(
                "PENDING",
                "SENT",
                "FAILED",
                name="delivery_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("last_delivery_error", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            ondelete="CASCADE",
            name="fk_signing_tokens_document_id_documents",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_signing_tokens"),
    )
    op.create_index("ix_signing_tokens_document_id", "signing_tokens", ["document_id"])
    op.create_index("ix_signing_tokens_token_hash", "signing_tokens", ["token_hash"], unique=True)

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("document_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("actor_type", sa.String(length=16), nullable=False),
        sa.Column("actor_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("actor_email", sa.String(length=320), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "actor_type IN ('SENDER', 'GUEST', 'SYSTEM')",
            name="actor_type",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            ondelete="RESTRICT",
            name="fk_audit_logs_document_id_documents",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
    )
    op.create_index("ix_audit_logs_document_id", "audit_logs", ["document_id"])
    op.create_index("ix_audit_logs_event_type", "audit_logs", ["event_type"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_event_type", table_name="audit_logs")
    op.drop_index("ix_audit_logs_document_id", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index("ix_signing_tokens_token_hash", table_name="signing_tokens")
    op.drop_index("ix_signing_tokens_document_id", table_name="signing_tokens")
    op.drop_table("signing_tokens")
    op.drop_index("ix_signature_fields_document_id", table_name="signature_fields")
    op.drop_table("signature_fields")
    op.drop_index("ix_documents_status", table_name="documents")
    op.drop_index("ix_documents_owner_id", table_name="documents")
    op.drop_table("documents")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
