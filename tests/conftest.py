import pytest


@pytest.fixture
def sample_releases():
    """Newest-first release list spanning the smart-policy cutoff."""
    return [
        {
            "tag": "1.7.2",
            "prerelease": False,
            "assets": [
                {
                    "filename": "app-arm64-v8a-release.apk",
                    "size_bytes": 16_000_000,
                    "upstream_url": "https://example/arm64",
                    "browser_download_url": "https://example/arm64",
                },
                {
                    "filename": "app-x86-release.apk",
                    "size_bytes": 18_000_000,
                    "upstream_url": "https://example/x86",
                    "browser_download_url": "https://example/x86",
                },
            ],
        },
        {
            "tag": "1.6.0",
            "prerelease": False,
            "assets": [
                {
                    "filename": "app-arm64-v8a-release.apk",
                    "size_bytes": 16_000_000,
                    "upstream_url": "https://example/160-arm64",
                    "browser_download_url": "https://example/160-arm64",
                },
                {
                    "filename": "app-x86_64-release.apk",
                    "size_bytes": 18_000_000,
                    "upstream_url": "https://example/160-x64",
                    "browser_download_url": "https://example/160-x64",
                },
            ],
        },
        {
            "tag": "1.6.0-rc1",
            "prerelease": True,
            "assets": [
                {
                    "filename": "bitchat-1.6.0-rc1.apk",
                    "size_bytes": 10_000_000,
                    "upstream_url": "https://example/rc1",
                    "browser_download_url": "https://example/rc1",
                },
            ],
        },
        {
            "tag": "1.5.1",
            "prerelease": False,
            "assets": [
                {
                    "filename": "bitchat-1.5.1.apk",
                    "size_bytes": 149_000_000,
                    "upstream_url": "https://example/151",
                    "browser_download_url": "https://example/151",
                },
                {
                    "filename": "app-arm64-v8a-release.apk",
                    "size_bytes": 150_000_000,
                    "upstream_url": "https://example/151-arm64",
                    "browser_download_url": "https://example/151-arm64",
                },
                {
                    "filename": "app-armeabi-v7a-release.apk",
                    "size_bytes": 140_000_000,
                    "upstream_url": "https://example/151-v7a",
                    "browser_download_url": "https://example/151-v7a",
                },
                {
                    "filename": "app-universal-release.apk",
                    "size_bytes": 200_000_000,
                    "upstream_url": "https://example/151-uni",
                    "browser_download_url": "https://example/151-uni",
                },
            ],
        },
    ]
