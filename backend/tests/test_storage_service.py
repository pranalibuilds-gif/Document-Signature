from __future__ import annotations

from io import BytesIO

import pytest
from botocore.exceptions import ClientError

from app.services import storage_service
from app.services.storage_service import S3Storage, StorageError, get_storage_service


class FakeS3Client:
    def __init__(self) -> None:
        self.saved: dict[str, object] | None = None
        self.deleted: tuple[str, str] | None = None
        self.data = b"%PDF-1.7"

    def put_object(self, **kwargs: object) -> None:
        self.saved = kwargs

    def get_object(self, **kwargs: object) -> dict[str, BytesIO]:
        return {"Body": BytesIO(self.data)}

    def delete_object(self, **kwargs: object) -> None:
        self.deleted = (str(kwargs["Bucket"]), str(kwargs["Key"]))


def test_s3_storage_saves_reads_and_deletes_private_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = FakeS3Client()
    captured_client: dict[str, object] = {}

    def create_client(service: str, **kwargs: object) -> FakeS3Client:
        captured_client.update(service=service, **kwargs)
        return fake_client

    monkeypatch.setattr(storage_service.boto3, "client", create_client)
    storage = S3Storage(
        bucket="private-bucket",
        region="us-east-1",
        endpoint_url="https://objects.example.test",
        access_key_id="access",
        secret_access_key="secret",
    )

    storage.save_bytes("originals/document.pdf", fake_client.data)
    loaded = storage.read_bytes("originals/document.pdf")
    storage.delete("originals/document.pdf")

    assert captured_client == {
        "service": "s3",
        "region_name": "us-east-1",
        "endpoint_url": "https://objects.example.test",
        "aws_access_key_id": "access",
        "aws_secret_access_key": "secret",
    }
    assert fake_client.saved == {
        "Bucket": "private-bucket",
        "Key": "originals/document.pdf",
        "Body": fake_client.data,
        "ContentType": "application/pdf",
    }
    assert loaded == fake_client.data
    assert fake_client.deleted == ("private-bucket", "originals/document.pdf")


def test_storage_factory_uses_s3_for_configured_object_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(storage_service.settings, "storage_backend", "s3")
    monkeypatch.setattr(storage_service.settings, "s3_bucket", "private-bucket")
    monkeypatch.setattr(storage_service.settings, "s3_region", "us-east-1")
    monkeypatch.setattr(
        storage_service.boto3,
        "client",
        lambda *args, **kwargs: FakeS3Client(),
    )

    storage = get_storage_service()

    assert isinstance(storage, S3Storage)
    assert storage.bucket == "private-bucket"


@pytest.mark.parametrize(
    "key",
    ["../outside.pdf", "C:/outside.pdf", r"C:\outside.pdf", "/absolute.pdf"],
)
def test_s3_storage_rejects_non_relative_keys(
    monkeypatch: pytest.MonkeyPatch,
    key: str,
) -> None:
    monkeypatch.setattr(storage_service.boto3, "client", lambda *args, **kwargs: FakeS3Client())
    storage = S3Storage(bucket="private-bucket", region="us-east-1")

    with pytest.raises(ValueError, match="relative object key"):
        storage.read_bytes(key)


def test_s3_storage_surfaces_provider_errors_without_provider_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingS3Client:
        def put_object(self, **kwargs: object) -> None:
            raise ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "sensitive details"}},
                "PutObject",
            )

    monkeypatch.setattr(
        storage_service.boto3,
        "client",
        lambda *args, **kwargs: FailingS3Client(),
    )
    storage = S3Storage(bucket="private-bucket", region="us-east-1")

    with pytest.raises(StorageError, match="could not save the file") as error:
        storage.save_bytes("originals/document.pdf", b"%PDF-1.7")

    assert "sensitive details" not in str(error.value)
