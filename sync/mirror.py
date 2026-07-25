from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import time
from pathlib import Path

import httpx

from sync.models import MirrorPlan, MirrorPlanItem
from sync.storage import (
    APK_CACHE_CONTROL,
    APK_CONTENT_TYPE,
    CHECKSUM_CACHE_CONTROL,
    Storage,
)

log = logging.getLogger(__name__)

RETRY_BACKOFF_S = (2, 8, 32)
CHUNK_SIZE = 1024 * 1024  # 1 MiB


class MirrorError(RuntimeError):
    pass


def stream_download_to_temp(
    url: str,
    *,
    expected_size: int,
    token: str | None = None,
    client: httpx.Client | None = None,
) -> tuple[Path, str]:
    """
    Stream an asset to a temp file while computing SHA-256.

    Never buffers the whole APK in memory. Verifies byte count before return.
    Caller must delete the temp path.
    """
    headers: dict[str, str] = {"User-Agent": "bitchat-mirror-sync"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["Accept"] = "application/octet-stream"

    owns = client is None
    http = client or httpx.Client(timeout=120.0, follow_redirects=True)

    fd, name = tempfile.mkstemp(prefix="bitchat-apk-", suffix=".apk")
    path = Path(name)
    digest = hashlib.sha256()
    written = 0

    try:
        with os.fdopen(fd, "wb") as out:
            with http.stream("GET", url, headers=headers) as resp:
                if resp.status_code >= 400:
                    raise MirrorError(
                        f"download failed HTTP {resp.status_code} for {url}"
                    )
                for chunk in resp.iter_bytes(CHUNK_SIZE):
                    if not chunk:
                        continue
                    out.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)

        if written != expected_size:
            raise MirrorError(
                f"size mismatch for {url}: got {written} bytes, "
                f"expected {expected_size} — refusing to upload partial file"
            )
        return path, digest.hexdigest()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        if owns:
            http.close()


def upload_apk(
    storage: Storage,
    *,
    path: Path,
    key: str,
    tag: str,
    sha256: str,
) -> None:
    storage.upload_file(
        str(path),
        key,
        content_type=APK_CONTENT_TYPE,
        metadata={"sha256": sha256, "upstream-tag": tag},
        cache_control=APK_CACHE_CONTROL,
    )


def mirror_one(
    storage: Storage,
    item: MirrorPlanItem,
    *,
    token: str | None = None,
    client: httpx.Client | None = None,
) -> str:
    """
    Download one asset and upload it to R2. Retries 3× with 2s/8s/32s backoff.
    Returns the SHA-256 hex digest on success.
    """
    last_exc: Exception | None = None
    for attempt, delay in enumerate((*RETRY_BACKOFF_S, None)):
        try:
            path, sha256 = stream_download_to_temp(
                item.upstream_url,
                expected_size=item.size_bytes,
                token=token,
                client=client,
            )
            try:
                upload_apk(
                    storage,
                    path=path,
                    key=item.object_key,
                    tag=item.tag,
                    sha256=sha256,
                )
            finally:
                path.unlink(missing_ok=True)
            return sha256
        except Exception as exc:
            last_exc = exc
            if delay is None:
                break
            log.warning(
                "mirror failed for %s/%s (attempt %s/3): %s; retry in %ss",
                item.tag,
                item.filename,
                attempt + 1,
                exc,
                delay,
            )
            time.sleep(delay)

    assert last_exc is not None
    raise MirrorError(
        f"giving up on {item.tag}/{item.filename} after 3 attempts: {last_exc}"
    ) from last_exc


def checksums_body(entries: list[tuple[str, str]]) -> bytes:
    """Build sha256sum -c compatible content: `<hash>  <filename>` per line."""
    lines = [f"{sha256}  {filename}" for sha256, filename in sorted(entries)]
    return ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")


def write_checksums_for_tag(
    storage: Storage,
    tag: str,
    entries: list[tuple[str, str]],
) -> None:
    body = checksums_body(entries)
    storage.put_bytes(
        f"checksums/{tag}.sha256",
        body,
        content_type="text/plain; charset=utf-8",
        cache_control=CHECKSUM_CACHE_CONTROL,
    )


def execute_mirror_plan(
    storage: Storage,
    plan: MirrorPlan,
    *,
    token: str | None = None,
) -> dict[tuple[str, str], str]:
    """
    Mirror plan.to_mirror one asset at a time.

    Returns {(tag, filename): sha256} for successes. Failures are appended to
    plan.failed and do not abort the run.
    """
    sha_by_asset: dict[tuple[str, str], str] = {}
    succeeded: list[MirrorPlanItem] = []

    # Prefill SHAs for assets already in R2 (from object metadata).
    for item in plan.already_present:
        existing = storage.get_sha256(item.object_key)
        if existing:
            sha_by_asset[(item.tag, item.filename)] = existing

    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        for item in list(plan.to_mirror):
            log.info(
                "mirroring %s/%s (%s bytes)",
                item.tag,
                item.filename,
                item.size_bytes,
            )
            try:
                sha256 = mirror_one(storage, item, token=token, client=client)
            except Exception as exc:
                log.error("asset failed: %s/%s — %s", item.tag, item.filename, exc)
                failed = MirrorPlanItem(**{**item.__dict__, "reason": f"error:{exc}"})
                plan.failed.append(failed)
                continue
            sha_by_asset[(item.tag, item.filename)] = sha256
            succeeded.append(item)
            log.info("ok %s/%s sha256=%s", item.tag, item.filename, sha256[:16])

    # Drop failed items from to_mirror so the index only marks successes.
    failed_keys = {(i.tag, i.filename) for i in plan.failed}
    plan.to_mirror = [
        i for i in plan.to_mirror if (i.tag, i.filename) not in failed_keys
    ]

    # Write per-tag checksum files for tags we touched this run.
    tags = {i.tag for i in succeeded} | {
        i.tag for i in plan.already_present if (i.tag, i.filename) in sha_by_asset
    }
    for tag in sorted(tags):
        entries = [
            (sha, filename)
            for (t, filename), sha in sha_by_asset.items()
            if t == tag
        ]
        if entries:
            write_checksums_for_tag(storage, tag, entries)

    return sha_by_asset
