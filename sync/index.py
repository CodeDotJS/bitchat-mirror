from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sync.models import Asset, Budget, MirrorPlan, Release
from sync.policy import classify_abi, object_key


def serialize_index(index: dict[str, Any]) -> bytes:
    """Stable JSON bytes for public/releases.json and R2."""
    return (json.dumps(index, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def write_local_index(path: Path, index: dict[str, Any]) -> bool:
    """
    Write index to the repo path. Returns True if the file content changed
    (or the file did not exist yet).
    """
    body = serialize_index(index)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == body:
        return False
    path.write_bytes(body)
    return True


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_releases_index(
    *,
    repo: str,
    public_base: str,
    normalized_releases: list[dict[str, Any]],
    plan: MirrorPlan,
    max_bytes: int,
    generated_at: str | None = None,
    sha256_by_asset: dict[tuple[str, str], str] | None = None,
) -> dict[str, Any]:
    """
    Build the complete releases.json document.

    Every upstream release appears. Mirrored status comes from the plan:
    - to_mirror and already_present → mirrored (url points at R2)
    - everything else → mirrored=false, url=null
    """
    mirrored_keys: dict[tuple[str, str], str] = {}
    sha_lookup: dict[tuple[str, str], str | None] = dict(sha256_by_asset or {})

    for item in plan.to_mirror + plan.already_present:
        mirrored_keys[(item.tag, item.filename)] = item.object_key

    base = public_base.rstrip("/")
    releases: list[Release] = []
    used_bytes = 0

    for rel in normalized_releases:
        assets: list[Asset] = []
        for raw_asset in rel.get("assets", []):
            filename = raw_asset["filename"]
            size = int(raw_asset["size_bytes"])
            abi = classify_abi(filename)
            key = (rel["tag"], filename)
            is_mirrored = key in mirrored_keys
            url = None
            if is_mirrored:
                url = f"{base}/{object_key(rel['tag'], filename)}"
                used_bytes += size
            assets.append(
                Asset(
                    filename=filename,
                    abi=abi,
                    size_bytes=size,
                    sha256=sha_lookup.get(key),
                    mirrored=is_mirrored,
                    url=url,
                    upstream_url=raw_asset["upstream_url"],
                    browser_download_url=raw_asset.get("browser_download_url", ""),
                )
            )
        releases.append(
            Release(
                tag=rel["tag"],
                name=rel["name"],
                published_at=rel["published_at"],
                prerelease=rel["prerelease"],
                commit_sha=rel.get("commit_sha"),
                upstream_url=rel["upstream_url"],
                notes_md=rel.get("notes_md") or "",
                assets=assets,
            )
        )

    budget = Budget(used_bytes=used_bytes, max_bytes=max_bytes)
    return {
        "generated_at": generated_at or utc_now_iso(),
        "repo": repo,
        "public_base": base,
        "budget": budget.to_index_dict(),
        "releases": [r.to_index_dict() for r in releases],
    }
