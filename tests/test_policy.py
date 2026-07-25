from sync.policy import (
    MirrorPolicy,
    Version,
    classify_abi,
    plan_mirrors,
    version_ge,
)


class TestClassifyAbi:
    def test_arm64_patterns(self):
        assert classify_abi("app-arm64-v8a-release.apk") == "arm64-v8a"
        assert classify_abi("bitchat-aarch64.apk") == "arm64-v8a"

    def test_armeabi_patterns(self):
        assert classify_abi("app-armeabi-v7a-release.apk") == "armeabi-v7a"
        assert classify_abi("foo-armv7.apk") == "armeabi-v7a"
        assert classify_abi("foo-arm32.apk") == "armeabi-v7a"

    def test_x86_64_before_x86(self):
        assert classify_abi("app-x86_64-release.apk") == "x86_64"
        assert classify_abi("app-x86-64-release.apk") == "x86_64"
        assert classify_abi("app-x86-release.apk") == "x86"

    def test_universal(self):
        assert classify_abi("app-universal-release.apk") == "universal"
        assert classify_abi("bitchat-all.apk") == "universal"

    def test_fallback_null(self):
        assert classify_abi("bitchat-1.5.1.apk") is None
        assert classify_abi("notes.txt") is None


class TestVersion:
    def test_prerelease_sorts_below_release(self):
        assert Version.parse("1.6.0-rc1") < Version.parse("1.6.0")
        assert not version_ge("1.6.0-rc1", "1.6.0")
        assert version_ge("1.6.0", "1.6.0")
        assert version_ge("1.7.0", "1.6.0")

    def test_numeric_ordering(self):
        assert Version.parse("1.5.1") < Version.parse("1.6.0")
        assert Version.parse("1.6.0") < Version.parse("1.6.1")


class TestSmartPolicy:
    def test_smart_splits_at_1_6_0(self, sample_releases):
        plan = plan_mirrors(
            releases=sample_releases,
            policy=MirrorPolicy.parse("smart"),
            max_bytes=10_000_000_000,
            already_mirrored=set(),
        )
        mirrored = {(i.tag, i.filename) for i in plan.to_mirror}

        # >= 1.6.0: every ABI
        assert ("1.7.2", "app-arm64-v8a-release.apk") in mirrored
        assert ("1.7.2", "app-x86-release.apk") in mirrored
        assert ("1.6.0", "app-x86_64-release.apk") in mirrored

        # < 1.6.0: only arm64-v8a + universal
        assert ("1.5.1", "app-arm64-v8a-release.apk") in mirrored
        assert ("1.5.1", "app-universal-release.apk") in mirrored
        assert ("1.5.1", "app-armeabi-v7a-release.apk") not in mirrored
        assert ("1.5.1", "bitchat-1.5.1.apk") not in mirrored  # abi=null

        # 1.6.0-rc1 sorts below 1.6.0 → old policy; unlabeled → skipped
        assert ("1.6.0-rc1", "bitchat-1.6.0-rc1.apk") not in mirrored

    def test_byte_budget_stops_at_right_asset(self, sample_releases):
        # First asset of newest release is 16 MB — budget of 20 MB allows it,
        # second (18 MB) must be skipped.
        plan = plan_mirrors(
            releases=sample_releases[:1],
            policy=MirrorPolicy.parse("all"),
            max_bytes=20_000_000,
            already_mirrored=set(),
        )
        assert len(plan.to_mirror) == 1
        assert plan.to_mirror[0].filename == "app-arm64-v8a-release.apk"
        assert len(plan.skipped_budget) == 1
        assert plan.skipped_budget[0].filename == "app-x86-release.apk"

    def test_latest_n_stable_only(self, sample_releases):
        plan = plan_mirrors(
            releases=sample_releases,
            policy=MirrorPolicy.parse("latest:1"),
            max_bytes=10_000_000_000,
            already_mirrored=set(),
        )
        tags = {i.tag for i in plan.to_mirror}
        assert tags == {"1.7.2"}
