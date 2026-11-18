from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Protocol

import boto3
from botocore.client import BaseClient
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings


class StorageError(OSError):
    """Raised when an object-storage operation fails."""


class StorageBackend(Protocol):
    def save_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/pdf",
    ) -> None: ...

    def read_bytes(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...


class LocalDiskStorage:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir.resolve()

    def _resolve_key(self, key: str) -> Path:
        windows_path = PureWindowsPath(key)
        posix_path = PurePosixPath(key.replace("\\", "/"))
        if (
            not key
            or windows_path.drive
            or windows_path.root
            or posix_path.is_absolute()
            or any(part == ".." for part in posix_path.parts)
        ):
            raise ValueError("Storage key must be a non-empty relative path")
        relative_parts = [part for part in posix_path.parts if part not in {"", "."}]
        if not relative_parts:
            raise ValueError("Storage key must name a file")
        target_path = self.base_dir.joinpath(*relative_parts).resolve()
        if not target_path.is_relative_to(self.base_dir):
            raise ValueError("Storage key must remain within the storage directory")
        return target_path

    def save_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/pdf",
    ) -> None:
        del content_type
        target_path = self._resolve_key(key)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(data)

    def read_bytes(self, key: str) -> bytes:
        return self._resolve_key(key).read_bytes()

    def delete(self, key: str) -> None:
        self._resolve_key(key).unlink(missing_ok=True)


class S3Storage:
    def __init__(
        self,
        *,
        bucket: str,
        region: str,
        endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
    ) -> None:
        self.bucket = bucket
        self.client: BaseClient = boto3.client(
            "s3",
            region_name=region,
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
        )

    @staticmethod
    def _validate_key(key: str) -> str:
        path = PurePosixPath(key)
        windows_path = PureWindowsPath(key)
        if (
            not key
            or "\\" in key
            or windows_path.drive
            or windows_path.root
            or path.is_absolute()
            or any(part == ".." for part in path.parts)
            or not path.parts
        ):
            raise ValueError("Storage key must be a non-empty relative object key")
        return path.as_posix()

    def save_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/pdf",
    ) -> None:
        object_key = self._validate_key(key)
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=object_key,
                Body=data,
                ContentType=content_type,
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageError("Object storage could not save the file") from exc

    def read_bytes(self, key: str) -> bytes:
        object_key = self._validate_key(key)
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=object_key)
            body = response["Body"]
            try:
                return body.read()
            finally:
                body.close()
        except (BotoCoreError, ClientError) as exc:
            raise StorageError("Object storage could not read the file") from exc

    def delete(self, key: str) -> None:
        object_key = self._validate_key(key)
        try:
            self.client.delete_object(Bucket=self.bucket, Key=object_key)
        except (BotoCoreError, ClientError) as exc:
            raise StorageError("Object storage could not delete the file") from exc


def get_storage_service() -> StorageBackend:
    if settings.storage_backend == "local":
        return LocalDiskStorage(settings.local_storage_dir)
    if settings.storage_backend in {"s3", "supabase"}:
        if not settings.s3_bucket:
            raise RuntimeError("S3_BUCKET is required for object storage")
        if (
            settings.storage_backend == "supabase"
            and (
                not settings.s3_endpoint_url
                or settings.s3_access_key_id is None
                or settings.s3_secret_access_key is None
            )
        ):
            raise RuntimeError("Supabase S3 endpoint and credentials are required")
        return S3Storage(
            bucket=settings.s3_bucket,
            region=settings.s3_region,
            endpoint_url=settings.s3_endpoint_url,
            access_key_id=(
                settings.s3_access_key_id.get_secret_value()
                if settings.s3_access_key_id
                else None
            ),
            secret_access_key=(
                settings.s3_secret_access_key.get_secret_value()
                if settings.s3_secret_access_key
                else None
            ),
        )
    raise RuntimeError(f"Unsupported storage backend: {settings.storage_backend}")
