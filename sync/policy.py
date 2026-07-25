from __future__ import annotations

import re
from dataclasses import dataclass
from functools import total_ordering

from sync.models import MirrorPlan, MirrorPlanItem

# First match wins — x86_64 must precede bare x86.
_ABI_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"arm64|aarch64", re.I), "arm64-v8a"),
    (re.compile(r"armeabi|armv7|arm32", re.I), "armeabi-v7a"),
    (re.compile(r"x86[_-]?64", re.I), "x86_64"),
    (re.compile(r"x86", re.I), "x86"),
    (re.compile(r"universal|\ball\b", re.I), "universal"),
]

_SMART_CUTOFF = "1.6.0"
_SMART_OLD_ABIS = frozenset({"arm64-v8a", "universal"})


def classify_abi(filename: str) -> str | None:
    """Classify an APK filename into an Android ABI, or None if unknown."""
    for pattern, abi in _ABI_PATTERNS:
        if pattern.search(filename):
            return abi
    return None


@total_ordering
@dataclass(frozen=True)
class Version:
    """Loose semver for release tags. Prerelease sorts below the same base."""

    major: int
    minor: int
    patch: int
    prerelease: str | None = None

    @classmethod
    def parse(cls, tag: str) -> Version:
        raw = tag.strip()
        if raw.startswith("v") or raw.startswith("V"):
            raw = raw[1:]
        # Split prerelease on first '-' or '_' after the numeric core.
        m = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-_](.+))?$", raw)
        if not m:
            return cls(0, 0, 0, raw)
        major = int(m.group(1))
        minor = int(m.group(2) or 0)
        patch = int(m.group(3) or 0)
        pre = m.group(4) or None
        return cls(major, minor, patch, pre)

    def _base(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        if self._base() != other._base():
            return self._base() < other._base()
        # No prerelease > has prerelease (1.6.0 > 1.6.0-rc1)
        if self.prerelease is None and other.prerelease is None:
            return False
        if self.prerelease is None:
            return False
        if other.prerelease is None:
            return True
        return self.prerelease < other.prerelease

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return (
            self.major == other.major
            and self.minor == other.minor
            and self.patch == other.patch
            and self.prerelease == other.prerelease
        )


def version_ge(tag: str, cutoff: str) -> bool:
    return Version.parse(tag) >= Version.parse(cutoff)


@dataclass(frozen=True)
class MirrorPolicy:
    """Which assets to select for mirroring."""

    kind: str  # smart | all | latest:N
    latest_n: int | None = None

    @classmethod
    def parse(cls, value: str) -> MirrorPolicy:
        value = (value or "smart").strip().lower()
        if value == "smart":
            return cls("smart")
        if value == "all":
            return cls("all")
        if value.startswith("latest:"):
            n = int(value.split(":", 1)[1])
            if n < 1:
                raise ValueError("latest:N requires N >= 1")
            return cls("latest", latest_n=n)
        raise ValueError(f"Unknown MIRROR_POLICY: {value!r}")


def asset_allowed_by_policy(
    *,
    tag: str,
    abi: str | None,
    prerelease: bool,
    policy: MirrorPolicy,
    stable_rank: int | None = None,
) -> bool:
    """Return True if this APK should be considered for mirroring under policy."""
    if policy.kind == "all":
        return True
    if policy.kind == "latest":
        # Only stable releases in the top N (rank assigned by caller, 0-based).
        if prerelease or stable_rank is None:
            return False
        assert policy.latest_n is not None
        return stable_rank < policy.latest_n
    # smart
    if version_ge(tag, _SMART_CUTOFF):
        return True
    return abi in _SMART_OLD_ABIS


def object_key(tag: str, filename: str) -> str:
    return f"apk/{tag}/{filename}"


def plan_mirrors(
    *,
    releases: list[dict],
    policy: MirrorPolicy,
    max_bytes: int,
    already_mirrored: set[tuple[str, str, int]],
) -> MirrorPlan:
    """
    Build a mirror plan.

    `releases` is a list of dicts with keys:
      tag, prerelease, assets: [{filename, size_bytes, upstream_url, browser_download_url}]
    Newest-first order is expected so the budget prefers what people want.
    `already_mirrored` is a set of (tag, filename, size_bytes) known present in R2.
    """
    plan = MirrorPlan()
    used = 0

    # Rank stable releases newest-first for latest:N.
    stable_ranks: dict[str, int] = {}
    rank = 0
    for rel in releases:
        if not rel.get("prerelease"):
            stable_ranks[rel["tag"]] = rank
            rank += 1

    for rel in releases:
        tag = rel["tag"]
        prerelease = bool(rel.get("prerelease"))
        stable_rank = stable_ranks.get(tag)
        for asset in rel.get("assets", []):
            filename = asset["filename"]
            if not filename.lower().endswith(".apk"):
                continue
            size = int(asset["size_bytes"])
            abi = classify_abi(filename)
            upstream = asset["upstream_url"]
            key = object_key(tag, filename)
            item = MirrorPlanItem(
                tag=tag,
                filename=filename,
                abi=abi,
                size_bytes=size,
                upstream_url=upstream,
                object_key=key,
            )

            allowed = asset_allowed_by_policy(
                tag=tag,
                abi=abi,
                prerelease=prerelease,
                policy=policy,
                stable_rank=stable_rank,
            )
            if not allowed:
                item = MirrorPlanItem(**{**item.__dict__, "reason": "policy"})
                plan.skipped_policy.append(item)
                continue

            if (tag, filename, size) in already_mirrored:
                item = MirrorPlanItem(**{**item.__dict__, "reason": "already_present"})
                plan.already_present.append(item)
                used += size
                continue

            if used + size > max_bytes:
                item = MirrorPlanItem(**{**item.__dict__, "reason": "budget"})
                plan.skipped_budget.append(item)
                continue

            plan.to_mirror.append(item)
            used += size

    return plan
