import httpx
import pytest
import respx

from sync.github import API_BASE, GitHubClient, GitHubError, RateLimitError

REPO = "permissionlesstech/bitchat-android"


def _release(tag: str, n: int = 1) -> dict:
    return {
        "tag_name": tag,
        "name": tag,
        "published_at": "2026-01-01T00:00:00Z",
        "prerelease": False,
        "html_url": f"https://github.com/{REPO}/releases/tag/{tag}",
        "body": "notes",
        "assets": [
            {
                "name": "app-arm64-v8a-release.apk",
                "size": 1000 * n,
                "browser_download_url": f"https://github.com/download/{tag}.apk",
            }
        ],
    }


@respx.mock
def test_pagination_across_pages():
    page1 = [_release(f"1.0.{i}") for i in range(100)]
    page2 = [_release("0.9.0")]
    page3: list = []

    route = respx.get(f"{API_BASE}/repos/{REPO}/releases").mock(
        side_effect=[
            httpx.Response(200, json=page1),
            httpx.Response(200, json=page2),
            httpx.Response(200, json=page3),
        ]
    )

    with GitHubClient(repo=REPO, token="test", max_retries=1) as gh:
        releases = gh.list_releases()

    assert len(releases) == 101
    assert route.call_count == 2  # page3 not fetched because page2 < per_page
    assert releases[-1]["tag_name"] == "0.9.0"


@respx.mock
def test_annotated_tag_dereferencing():
    tag_sha = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    commit_sha = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

    respx.get(f"{API_BASE}/repos/{REPO}/git/ref/tags/1.7.2").mock(
        return_value=httpx.Response(
            200,
            json={
                "ref": "refs/tags/1.7.2",
                "object": {"type": "tag", "sha": tag_sha},
            },
        )
    )
    respx.get(f"{API_BASE}/repos/{REPO}/git/tags/{tag_sha}").mock(
        return_value=httpx.Response(
            200,
            json={
                "sha": tag_sha,
                "object": {"type": "commit", "sha": commit_sha},
            },
        )
    )

    with GitHubClient(repo=REPO, token="test", max_retries=1) as gh:
        assert gh.resolve_commit_sha("1.7.2") == commit_sha


@respx.mock
def test_lightweight_tag():
    commit_sha = "cccccccccccccccccccccccccccccccccccccccc"
    respx.get(f"{API_BASE}/repos/{REPO}/git/ref/tags/1.5.1").mock(
        return_value=httpx.Response(
            200,
            json={
                "ref": "refs/tags/1.5.1",
                "object": {"type": "commit", "sha": commit_sha},
            },
        )
    )

    with GitHubClient(repo=REPO, token="test", max_retries=1) as gh:
        assert gh.resolve_commit_sha("1.5.1") == commit_sha


@respx.mock
def test_rate_limit_sleeps_when_authenticated(monkeypatch):
    """Authenticated clients wait briefly, then retry."""
    slept: list[int] = []
    monkeypatch.setattr("sync.github.time.sleep", lambda s: slept.append(s))

    route = respx.get(f"{API_BASE}/repos/{REPO}/releases").mock(
        side_effect=[
            httpx.Response(
                403,
                json={"message": "API rate limit exceeded"},
                headers={
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": "0",
                },
            ),
            httpx.Response(200, json=[_release("1.0.0")]),
        ]
    )

    with GitHubClient(repo=REPO, token="test", max_retries=3) as gh:
        releases = gh.list_releases()

    assert len(releases) == 1
    assert route.call_count == 2
    assert slept


@respx.mock
def test_rate_limit_fails_fast_when_anonymous(monkeypatch):
    """Anonymous clients must not sleep on a dead 60/h quota."""
    slept: list[int] = []
    monkeypatch.setattr("sync.github.time.sleep", lambda s: slept.append(s))

    respx.get(f"{API_BASE}/repos/{REPO}/releases").mock(
        return_value=httpx.Response(
            403,
            json={"message": "API rate limit exceeded"},
            headers={
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": "9999999999",
            },
        )
    )

    with GitHubClient(repo=REPO, token=None, max_retries=3) as gh:
        with pytest.raises(RateLimitError, match="GITHUB_TOKEN"):
            gh.list_releases()

    assert slept == []


@respx.mock
def test_resolve_commit_shas_batches_via_tags_list():
    """Bulk path uses /tags — not one /git/ref call per release."""
    tags_route = respx.get(f"{API_BASE}/repos/{REPO}/tags").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"name": "1.7.2", "commit": {"sha": "a" * 40}},
                {"name": "1.5.1", "commit": {"sha": "b" * 40}},
            ],
        )
    )
    refs_route = respx.get(url__regex=rf"{API_BASE}/repos/{REPO}/git/ref/tags/.*")

    with GitHubClient(repo=REPO, token="test", max_retries=1) as gh:
        mapping = gh.resolve_commit_shas(["1.7.2", "1.5.1"])

    assert mapping == {"1.7.2": "a" * 40, "1.5.1": "b" * 40}
    assert tags_route.call_count == 1
    assert refs_route.call_count == 0


@respx.mock
def test_resolve_commit_shas_falls_back_for_missing_tags():
    commit_sha = "c" * 40
    respx.get(f"{API_BASE}/repos/{REPO}/tags").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get(f"{API_BASE}/repos/{REPO}/git/ref/tags/orphan").mock(
        return_value=httpx.Response(
            200,
            json={
                "ref": "refs/tags/orphan",
                "object": {"type": "commit", "sha": commit_sha},
            },
        )
    )

    with GitHubClient(repo=REPO, token="test", max_retries=1) as gh:
        mapping = gh.resolve_commit_shas(["orphan"])

    assert mapping == {"orphan": commit_sha}


@respx.mock
def test_http_error_raises():
    respx.get(f"{API_BASE}/repos/{REPO}/releases").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )
    with GitHubClient(repo=REPO, token="test", max_retries=1) as gh:
        with pytest.raises(GitHubError, match="404"):
            gh.list_releases()
