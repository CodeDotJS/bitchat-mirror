"""Tests for stream download + R2 upload (Stage 2)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import boto3
import httpx
import pytest
import respx
from moto import mock_aws

from sync.mirror import (
    checksums_body,
    execute_mirror_plan,
    mirror_one,
    stream_download_to_temp,
)
from sync.models import MirrorPlan, MirrorPlanItem
from sync.storage import APK_CONTENT_TYPE, R2Storage


@pytest.fixture
def apk_bytes() -> bytes:
    return b"fake-apk-payload-" + b"\x00" * 100


@pytest.fixture
def r2_storage():
    with mock_aws():
        conn = boto3.client("s3", region_name="us-east-1")
        conn.create_bucket(Bucket="bitchat-mirror")
        yield R2Storage(
            account_id="test",
            access_key_id="testing",
            secret_access_key="testing",
            bucket="bitchat-mirror",
            client=conn,
        )


@respx.mock
def test_stream_download_hashes_and_checks_size(apk_bytes: bytes, tmp_path: Path):
    url = "https://github.com/download/app.apk"
    respx.get(url).mock(return_value=httpx.Response(200, content=apk_bytes))

    path, sha = stream_download_to_temp(url, expected_size=len(apk_bytes))
    try:
        assert path.read_bytes() == apk_bytes
        assert sha == hashlib.sha256(apk_bytes).hexdigest()
    finally:
        path.unlink(missing_ok=True)


@respx.mock
def test_stream_download_rejects_partial(apk_bytes: bytes):
    url = "https://github.com/download/app.apk"
    respx.get(url).mock(return_value=httpx.Response(200, content=apk_bytes))

    with pytest.raises(Exception, match="size mismatch"):
        stream_download_to_temp(url, expected_size=len(apk_bytes) + 50)


@respx.mock
def test_mirror_one_uploads_with_metadata(r2_storage: R2Storage, apk_bytes: bytes):
    url = "https://github.com/download/app-arm64.apk"
    respx.get(url).mock(return_value=httpx.Response(200, content=apk_bytes))
    item = MirrorPlanItem(
        tag="1.7.4",
        filename="app-arm64-v8a-release.apk",
        abi="arm64-v8a",
        size_bytes=len(apk_bytes),
        upstream_url=url,
        object_key="apk/1.7.4/app-arm64-v8a-release.apk",
    )

    sha = mirror_one(r2_storage, item)
    assert sha == hashlib.sha256(apk_bytes).hexdigest()
    assert r2_storage.object_exists_matching(item.object_key, len(apk_bytes))
    assert r2_storage.get_sha256(item.object_key) == sha

    head = r2_storage._client.head_object(
        Bucket="bitchat-mirror", Key=item.object_key
    )
    assert head["ContentType"] == APK_CONTENT_TYPE
    meta = {k.lower(): v for k, v in head["Metadata"].items()}
    assert meta["sha256"] == sha
    assert meta["upstream-tag"] == "1.7.4"


@respx.mock
def test_execute_plan_continues_after_one_failure(
    r2_storage: R2Storage, apk_bytes: bytes, monkeypatch
):
    monkeypatch.setattr("sync.mirror.RETRY_BACKOFF_S", (0, 0, 0))
    good_url = "https://github.com/download/good.apk"
    bad_url = "https://github.com/download/bad.apk"
    respx.get(good_url).mock(return_value=httpx.Response(200, content=apk_bytes))
    respx.get(bad_url).mock(return_value=httpx.Response(500, content=b"nope"))

    plan = MirrorPlan(
        to_mirror=[
            MirrorPlanItem(
                tag="1.7.4",
                filename="bad.apk",
                abi="arm64-v8a",
                size_bytes=len(apk_bytes),
                upstream_url=bad_url,
                object_key="apk/1.7.4/bad.apk",
            ),
            MirrorPlanItem(
                tag="1.7.4",
                filename="good.apk",
                abi="x86",
                size_bytes=len(apk_bytes),
                upstream_url=good_url,
                object_key="apk/1.7.4/good.apk",
            ),
        ]
    )

    shas = execute_mirror_plan(r2_storage, plan)
    assert ("1.7.4", "good.apk") in shas
    assert ("1.7.4", "bad.apk") not in shas
    assert len(plan.failed) == 1
    assert len(plan.to_mirror) == 1
    assert plan.to_mirror[0].filename == "good.apk"

    # Checksums written for successful assets of the tag.
    body = r2_storage._client.get_object(
        Bucket="bitchat-mirror", Key="checksums/1.7.4.sha256"
    )["Body"].read()
    assert hashlib.sha256(apk_bytes).hexdigest().encode() in body
    assert b"good.apk" in body
    assert b"bad.apk" not in body


def test_checksums_body_sha256sum_format():
    body = checksums_body(
        [
            ("bbbb", "b.apk"),
            ("aaaa", "a.apk"),
        ]
    )
    assert body == b"aaaa  a.apk\nbbbb  b.apk\n"
