from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Asset:
    filename: str
    abi: str | None
    size_bytes: int
    sha256: str | None
    mirrored: bool
    url: str | None
    upstream_url: str
    browser_download_url: str = ""

    def to_index_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "abi": self.abi,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "mirrored": self.mirrored,
            "url": self.url,
            "upstream_url": self.upstream_url,
        }


@dataclass
class Release:
    tag: str
    name: str
    published_at: str
    prerelease: bool
    commit_sha: str | None
    upstream_url: str
    notes_md: str
    assets: list[Asset] = field(default_factory=list)

    def to_index_dict(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "name": self.name,
            "published_at": self.published_at,
            "prerelease": self.prerelease,
            "commit_sha": self.commit_sha,
            "upstream_url": self.upstream_url,
            "notes_md": self.notes_md,
            "assets": [a.to_index_dict() for a in self.assets],
        }


@dataclass
class Budget:
    used_bytes: int
    max_bytes: int

    def remaining(self) -> int:
        return max(0, self.max_bytes - self.used_bytes)

    def to_index_dict(self) -> dict[str, int]:
        return {"used_bytes": self.used_bytes, "max_bytes": self.max_bytes}


@dataclass
class MirrorPlanItem:
    tag: str
    filename: str
    abi: str | None
    size_bytes: int
    upstream_url: str
    object_key: str
    reason: str = "mirror"


@dataclass
class MirrorPlan:
    """What a sync run would (or did) do."""

    to_mirror: list[MirrorPlanItem] = field(default_factory=list)
    already_present: list[MirrorPlanItem] = field(default_factory=list)
    skipped_policy: list[MirrorPlanItem] = field(default_factory=list)
    skipped_budget: list[MirrorPlanItem] = field(default_factory=list)
    failed: list[MirrorPlanItem] = field(default_factory=list)

    @property
    def projected_bytes(self) -> int:
        return sum(i.size_bytes for i in self.to_mirror) + sum(
            i.size_bytes for i in self.already_present
        )

    @property
    def new_bytes(self) -> int:
        return sum(i.size_bytes for i in self.to_mirror)


def release_to_dict(release: Release) -> dict[str, Any]:
    return asdict(release)
