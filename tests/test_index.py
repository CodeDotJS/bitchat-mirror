from pathlib import Path

from sync.index import build_releases_index, write_local_index
from sync.policy import MirrorPolicy, plan_mirrors


def _normalized(sample_releases):
    out = []
    for rel in sample_releases:
        out.append(
            {
                "tag": rel["tag"],
                "name": rel["tag"],
                "published_at": "2026-01-01T00:00:00Z",
                "prerelease": rel["prerelease"],
                "commit_sha": "abc123",
                "upstream_url": f"https://github.com/example/releases/tag/{rel['tag']}",
                "notes_md": f"notes for {rel['tag']}",
                "assets": rel["assets"],
            }
        )
    return out


class TestIndex:
    def test_unmirrored_still_appear(self, sample_releases):
        normalized = _normalized(sample_releases)
        plan = plan_mirrors(
            releases=sample_releases,
            policy=MirrorPolicy.parse("smart"),
            max_bytes=10_000_000_000,
            already_mirrored=set(),
        )
        index = build_releases_index(
            repo="permissionlesstech/bitchat-android",
            public_base="https://cdn.example",
            normalized_releases=normalized,
            plan=plan,
            max_bytes=10_000_000_000,
            generated_at="2026-07-24T09:00:00Z",
        )

        tags = [r["tag"] for r in index["releases"]]
        assert tags == ["1.7.2", "1.6.0", "1.6.0-rc1", "1.5.1"]

        # Unlabeled 1.5.1 APK is in the index but not mirrored
        old = next(r for r in index["releases"] if r["tag"] == "1.5.1")
        unlabeled = next(a for a in old["assets"] if a["filename"] == "bitchat-1.5.1.apk")
        assert unlabeled["mirrored"] is False
        assert unlabeled["url"] is None
        assert unlabeled["upstream_url"].startswith("https://")

        # Mirrored asset has R2 URL
        new = next(r for r in index["releases"] if r["tag"] == "1.7.2")
        arm = next(a for a in new["assets"] if "arm64" in a["filename"])
        assert arm["mirrored"] is True
        assert arm["url"] == (
            "https://cdn.example/apk/1.7.2/app-arm64-v8a-release.apk"
        )

    def test_generated_at_utc_iso(self, sample_releases):
        normalized = _normalized(sample_releases)
        plan = plan_mirrors(
            releases=sample_releases,
            policy=MirrorPolicy.parse("all"),
            max_bytes=10_000_000_000,
            already_mirrored=set(),
        )
        index = build_releases_index(
            repo="x/y",
            public_base="https://cdn.example",
            normalized_releases=normalized,
            plan=plan,
            max_bytes=10_000_000_000,
            generated_at="2026-07-24T09:00:00Z",
        )
        assert index["generated_at"] == "2026-07-24T09:00:00Z"
        assert index["generated_at"].endswith("Z")

    def test_schema_round_trip_keys(self, sample_releases):
        normalized = _normalized(sample_releases)
        plan = plan_mirrors(
            releases=sample_releases,
            policy=MirrorPolicy.parse("smart"),
            max_bytes=10_000_000_000,
            already_mirrored=set(),
        )
        index = build_releases_index(
            repo="permissionlesstech/bitchat-android",
            public_base="https://cdn.example/",
            normalized_releases=normalized,
            plan=plan,
            max_bytes=10200547328,
            generated_at="2026-07-24T09:00:00Z",
        )
        assert set(index.keys()) == {
            "generated_at",
            "repo",
            "public_base",
            "budget",
            "releases",
        }
        assert index["public_base"] == "https://cdn.example"
        asset = index["releases"][0]["assets"][0]
        assert set(asset.keys()) == {
            "filename",
            "abi",
            "size_bytes",
            "sha256",
            "mirrored",
            "url",
            "upstream_url",
        }

    def test_write_local_index_detects_change(self, sample_releases, tmp_path: Path):
        normalized = _normalized(sample_releases)
        plan = plan_mirrors(
            releases=sample_releases,
            policy=MirrorPolicy.parse("smart"),
            max_bytes=10_000_000_000,
            already_mirrored=set(),
        )
        index = build_releases_index(
            repo="x/y",
            public_base="https://cdn.example",
            normalized_releases=normalized,
            plan=plan,
            max_bytes=10_000_000_000,
            generated_at="2026-07-24T09:00:00Z",
        )
        path = tmp_path / "releases.json"
        assert write_local_index(path, index) is True
        assert write_local_index(path, index) is False
        index["generated_at"] = "2026-07-24T10:00:00Z"
        assert write_local_index(path, index) is True
