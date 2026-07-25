from __future__ import annotations

import logging
import os
from typing import Any, Protocol

log = logging.getLogger(__name__)

APK_CONTENT_TYPE = "application/vnd.android.package-archive"
# APKs are immutable once published under a tag/filename.
APK_CACHE_CONTROL = "public, max-age=31536000, immutable"
INDEX_CACHE_CONTROL = "public, max-age=60"
CHECKSUM_CACHE_CONTROL = "public, max-age=31536000, immutable"


class Storage(Protocol):
    def object_exists_matching(self, key: str, expected_size: int) -> bool: ...

    def upload_file(
        self,
        path: str,
        key: str,
        *,
        content_type: str,
        metadata: dict[str, str],
        cache_control: str,
    ) -> None: ...

    def put_bytes(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str,
        cache_control: str,
    ) -> None: ...

    def get_sha256(self, key: str) -> str | None: ...


class NullStorage:
    """No-op storage used for dry-run."""

    def object_exists_matching(self, key: str, expected_size: int) -> bool:
        return False

    def upload_file(
        self,
        path: str,
        key: str,
        *,
        content_type: str,
        metadata: dict[str, str],
        cache_control: str,
    ) -> None:
        raise RuntimeError("NullStorage cannot upload")

    def put_bytes(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str,
        cache_control: str,
    ) -> None:
        raise RuntimeError("NullStorage cannot upload")

    def get_sha256(self, key: str) -> str | None:
        return None


def _is_not_found(exc: BaseException) -> bool:
    response = getattr(exc, "response", None) or {}
    code = str(response.get("Error", {}).get("Code", ""))
    http_status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return code in {"404", "NoSuchKey", "NotFound"} or http_status == 404


class R2Storage:
    """S3-compatible client for Cloudflare R2."""

    def __init__(
        self,
        *,
        account_id: str,
        access_key_id: str,
        secret_access_key: str,
        bucket: str,
        client: object | None = None,
    ) -> None:
        self.bucket = bucket
        if client is not None:
            self._client: Any = client
        else:
            import boto3
            from botocore.config import Config

            endpoint = f"https://{account_id}.r2.cloudflarestorage.com"
            self._client = boto3.client(
                "s3",
                endpoint_url=endpoint,
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
                region_name="auto",
                config=Config(signature_version="s3v4"),
            )

    def object_exists_matching(self, key: str, expected_size: int) -> bool:
        from botocore.exceptions import ClientError

        try:
            head = self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _is_not_found(exc):
                return False
            raise
        size = int(head.get("ContentLength") or 0)
        meta = {k.lower(): v for k, v in (head.get("Metadata") or {}).items()}
        return size == expected_size and "sha256" in meta

    def get_sha256(self, key: str) -> str | None:
        from botocore.exceptions import ClientError

        try:
            head = self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _is_not_found(exc):
                return None
            raise
        meta = {k.lower(): v for k, v in (head.get("Metadata") or {}).items()}
        return meta.get("sha256")

    def upload_file(
        self,
        path: str,
        key: str,
        *,
        content_type: str,
        metadata: dict[str, str],
        cache_control: str,
    ) -> None:
        # boto3 lowercases metadata keys; values must be ASCII for S3 metadata.
        self._client.upload_file(
            path,
            self.bucket,
            key,
            ExtraArgs={
                "ContentType": content_type,
                "Metadata": metadata,
                "CacheControl": cache_control,
            },
        )
        log.info("uploaded s3://%s/%s", self.bucket, key)

    def put_bytes(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str,
        cache_control: str,
    ) -> None:
        self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
            CacheControl=cache_control,
        )
        log.info("put s3://%s/%s (%s bytes)", self.bucket, key, len(body))


def storage_from_env(*, dry_run: bool = False) -> Storage:
    if dry_run:
        log.info("dry-run: using NullStorage (skip R2 presence checks)")
        return NullStorage()

    account_id = os.environ.get("R2_ACCOUNT_ID", "").strip()
    access_key = os.environ.get("R2_ACCESS_KEY_ID", "").strip()
    secret = os.environ.get("R2_SECRET_ACCESS_KEY", "").strip()
    bucket = os.environ.get("R2_BUCKET", "").strip()
    missing = [
        name
        for name, val in [
            ("R2_ACCOUNT_ID", account_id),
            ("R2_ACCESS_KEY_ID", access_key),
            ("R2_SECRET_ACCESS_KEY", secret),
            ("R2_BUCKET", bucket),
        ]
        if not val
    ]
    if missing:
        raise SystemExit(
            "R2 credentials incomplete — missing "
            + ", ".join(missing)
            + ". Copy .env.example to .env and fill the R2_* values."
        )
    log.info("R2 storage ready bucket=%s", bucket)
    return R2Storage(
        account_id=account_id,
        access_key_id=access_key,
        secret_access_key=secret,
        bucket=bucket,
    )
