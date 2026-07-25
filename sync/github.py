from __future__ import annotations

import logging
import os
import time
from typing import Any, Iterator
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)

DEFAULT_REPO = "permissionlesstech/bitchat-android"
API_BASE = "https://api.github.com"
# Cap wait so a dead anonymous quota doesn't park the job for an hour.
MAX_RATE_LIMIT_SLEEP_S = 120


class GitHubError(RuntimeError):
    pass


class RateLimitError(GitHubError):
    """Raised when the GitHub API rate limit is exhausted."""

    def __init__(self, message: str, *, reset_at: int | None = None) -> None:
        super().__init__(message)
        self.reset_at = reset_at


def _is_rate_limited(resp: httpx.Response) -> bool:
    if resp.status_code != 403 and resp.status_code != 429:
        return False
    remaining = resp.headers.get("X-RateLimit-Remaining")
    if remaining == "0":
        return True
    body = (resp.text or "").lower()
    return "rate limit" in body or "secondary rate limit" in body


class GitHubClient:
    def __init__(
        self,
        repo: str = DEFAULT_REPO,
        token: str | None = None,
        client: httpx.Client | None = None,
        *,
        max_retries: int = 3,
        allow_rate_limit_sleep: bool | None = None,
    ) -> None:
        self.repo = repo
        self.token = token if token is not None else os.environ.get("GITHUB_TOKEN")
        self._owns_client = client is None
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "bitchat-mirror-sync",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        self._client = client or httpx.Client(
            base_url=API_BASE,
            headers=headers,
            timeout=30.0,
            follow_redirects=True,
        )
        self.max_retries = max_retries
        # Anonymous: 60 req/h — sleeping rarely helps a sync job; fail with guidance.
        # Authenticated: 5000 req/h — brief wait-and-retry is fine.
        if allow_rate_limit_sleep is None:
            allow_rate_limit_sleep = bool(self.token)
        self.allow_rate_limit_sleep = allow_rate_limit_sleep

        if self.token:
            log.info("GitHub auth: token present (5000 req/h)")
        else:
            log.warning(
                "GitHub auth: anonymous (60 req/h). Set GITHUB_TOKEN to avoid "
                "rate limits — Actions injects one automatically."
            )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _rate_limit_wait(self, resp: httpx.Response) -> None:
        reset = resp.headers.get("X-RateLimit-Reset")
        reset_at = int(reset) if reset and reset.isdigit() else None
        wait = 60
        if reset_at is not None:
            wait = max(1, reset_at - int(time.time()) + 1)
        wait = min(wait, MAX_RATE_LIMIT_SLEEP_S)

        if not self.allow_rate_limit_sleep:
            hint = (
                "Set GITHUB_TOKEN (a fine-grained or classic PAT with public_repo "
                "read is enough) and re-run. In GitHub Actions, GITHUB_TOKEN is "
                "injected automatically."
            )
            raise RateLimitError(
                f"GitHub API rate limit exceeded (anonymous). {hint}",
                reset_at=reset_at,
            )

        log.warning(
            "GitHub rate limit hit; sleeping %ss (reset_at=%s)", wait, reset_at
        )
        time.sleep(wait)

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            resp = self._client.request(method, path, **kwargs)
            remaining = resp.headers.get("X-RateLimit-Remaining")
            reset = resp.headers.get("X-RateLimit-Reset")
            if remaining is not None:
                log.debug("rate_limit remaining=%s reset=%s", remaining, reset)

            if _is_rate_limited(resp):
                try:
                    self._rate_limit_wait(resp)
                except RateLimitError:
                    raise
                last_exc = RateLimitError("GitHub rate limit exceeded")
                continue

            if resp.status_code in {502, 503, 504}:
                delay = 2**attempt
                log.warning(
                    "GitHub %s on %s; retry in %ss", resp.status_code, path, delay
                )
                time.sleep(delay)
                last_exc = GitHubError(f"HTTP {resp.status_code} for {path}")
                continue

            if resp.status_code >= 400:
                raise GitHubError(
                    f"GitHub API {resp.status_code} for {path}: {resp.text[:300]}"
                )
            return resp

        raise last_exc or GitHubError(f"Failed after retries: {path}")

    def iter_releases(self, per_page: int = 100) -> Iterator[dict[str, Any]]:
        """Yield every release, paginating until an empty page."""
        page = 1
        while True:
            resp = self._request(
                "GET",
                f"/repos/{self.repo}/releases",
                params={"per_page": per_page, "page": page},
            )
            batch = resp.json()
            if not batch:
                break
            log.info("fetched releases page=%s count=%s", page, len(batch))
            yield from batch
            if len(batch) < per_page:
                break
            page += 1

    def list_releases(self) -> list[dict[str, Any]]:
        return list(self.iter_releases())

    def list_tag_commits(self, per_page: int = 100) -> dict[str, str]:
        """
        Map tag name → commit SHA in a few paginated calls.

        Uses GET /repos/{repo}/tags, which returns the peeled commit SHA for
        both lightweight and annotated tags. Prefer this over one request per tag.
        """
        mapping: dict[str, str] = {}
        page = 1
        while True:
            resp = self._request(
                "GET",
                f"/repos/{self.repo}/tags",
                params={"per_page": per_page, "page": page},
            )
            batch = resp.json()
            if not batch:
                break
            for item in batch:
                name = item.get("name")
                sha = (item.get("commit") or {}).get("sha")
                if name and sha:
                    mapping[name] = sha
            log.info(
                "fetched tags page=%s count=%s total=%s", page, len(batch), len(mapping)
            )
            if len(batch) < per_page:
                break
            page += 1
        return mapping

    def resolve_commit_shas(self, tags: list[str]) -> dict[str, str | None]:
        """
        Resolve many release tags to commit SHAs with minimal API traffic.

        Primary path: one paginated /tags listing (~1–2 requests for this repo).
        Fallback: per-tag ref dereference only for names missing from that list.
        """
        wanted = set(tags)
        bulk = self.list_tag_commits()
        result: dict[str, str | None] = {tag: bulk.get(tag) for tag in tags}

        missing = [t for t in tags if result[t] is None]
        if missing:
            log.warning(
                "%s tag(s) missing from /tags listing; falling back to per-tag resolve",
                len(missing),
            )
            for tag in missing:
                result[tag] = self.resolve_commit_sha(tag)

        found = sum(1 for t in wanted if result.get(t))
        log.info("resolved commit SHAs: %s/%s tags", found, len(wanted))
        return result

    def resolve_commit_sha(self, tag: str) -> str | None:
        """
        Resolve a single release tag to a commit SHA.

        Lightweight tags: ref object type is 'commit'.
        Annotated tags: ref object type is 'tag' — dereference via /git/tags/{sha}.
        Prefer resolve_commit_shas() when resolving many tags.
        """
        encoded = "/".join(quote(part, safe="") for part in tag.split("/"))
        path = f"/repos/{self.repo}/git/ref/tags/{encoded}"
        try:
            resp = self._request("GET", path)
        except RateLimitError:
            raise
        except GitHubError as exc:
            log.warning("Could not resolve tag %s: %s", tag, exc)
            return None

        obj = resp.json()["object"]
        if obj["type"] == "commit":
            return obj["sha"]

        if obj["type"] == "tag":
            tag_resp = self._request(
                "GET", f"/repos/{self.repo}/git/tags/{obj['sha']}"
            )
            tag_obj = tag_resp.json()["object"]
            if tag_obj["type"] != "commit":
                log.warning(
                    "Annotated tag %s points to %s, not commit", tag, tag_obj["type"]
                )
                return None
            return tag_obj["sha"]

        log.warning("Unexpected ref object type %s for tag %s", obj["type"], tag)
        return None


def normalize_release(raw: dict[str, Any], commit_sha: str | None) -> dict[str, Any]:
    """Normalize a GitHub release payload into the shape policy/index expect."""
    assets = []
    for a in raw.get("assets", []):
        name = a.get("name") or ""
        if not name.lower().endswith(".apk"):
            continue
        assets.append(
            {
                "filename": name,
                "size_bytes": int(a["size"]),
                "upstream_url": a["browser_download_url"],
                "browser_download_url": a["browser_download_url"],
            }
        )
    return {
        "tag": raw["tag_name"],
        "name": raw.get("name") or raw["tag_name"],
        "published_at": raw.get("published_at") or "",
        "prerelease": bool(raw.get("prerelease")),
        "commit_sha": commit_sha,
        "upstream_url": raw.get("html_url")
        or f"https://github.com/{DEFAULT_REPO}/releases/tag/{raw['tag_name']}",
        "notes_md": raw.get("body") or "",
        "assets": assets,
    }
