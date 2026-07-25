from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from sync.github import GitHubClient, normalize_release
from sync.index import build_releases_index, serialize_index, write_local_index
from sync.mirror import execute_mirror_plan
from sync.models import MirrorPlan
from sync.policy import MirrorPolicy, plan_mirrors
from sync.storage import INDEX_CACHE_CONTROL, Storage, storage_from_env

log = logging.getLogger(__name__)

DEFAULT_MAX_BYTES = 10_200_547_328  # 9.5 GiB
REPO_ROOT = Path(__file__).resolve().parent.parent
PUBLIC_INDEX = REPO_ROOT / "public" / "releases.json"


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _fmt_bytes(n: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    value = float(n)
    for unit in units:
        if abs(value) < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{n} B"


def collect_already_mirrored(
    storage: Storage, normalized: list[dict[str, Any]]
) -> set[tuple[str, str, int]]:
    present: set[tuple[str, str, int]] = set()
    from sync.policy import object_key

    for rel in normalized:
        for asset in rel["assets"]:
            key = object_key(rel["tag"], asset["filename"])
            size = int(asset["size_bytes"])
            if storage.object_exists_matching(key, size):
                present.add((rel["tag"], asset["filename"], size))
    return present


def _write_index(storage: Storage | None, index: dict[str, Any], *, upload: bool) -> bool:
    """
    Write public/releases.json locally. Optionally mirror it to R2.
    Returns True if the local file content changed.
    """
    changed = write_local_index(PUBLIC_INDEX, index)
    if changed:
        log.info("wrote %s (changed)", PUBLIC_INDEX)
    else:
        log.info("%s unchanged", PUBLIC_INDEX)

    if upload and storage is not None:
        storage.put_bytes(
            "index/releases.json",
            serialize_index(index),
            content_type="application/json",
            cache_control=INDEX_CACHE_CONTROL,
        )
    return changed


def run(
    *,
    dry_run: bool = False,
    release: str | None = None,
    write_index: bool = False,
) -> int:
    repo = os.environ.get("REPO", "permissionlesstech/bitchat-android")
    public_base = os.environ.get("R2_PUBLIC_BASE", "https://example.invalid")
    policy = MirrorPolicy.parse(os.environ.get("MIRROR_POLICY", "smart"))
    max_bytes = int(os.environ.get("MAX_BYTES", str(DEFAULT_MAX_BYTES)))

    log.info(
        "starting sync dry_run=%s release=%s repo=%s policy=%s max_bytes=%s",
        dry_run,
        release or "*",
        repo,
        os.environ.get("MIRROR_POLICY", "smart"),
        _fmt_bytes(max_bytes),
    )

    with GitHubClient(repo=repo) as gh:
        raw_releases = gh.list_releases()
        if not raw_releases:
            raise SystemExit(f"No releases found for {repo}")

        tags = [raw["tag_name"] for raw in raw_releases]
        sha_by_tag = gh.resolve_commit_shas(tags)

        normalized: list[dict[str, Any]] = []
        for raw in raw_releases:
            tag = raw["tag_name"]
            normalized.append(normalize_release(raw, sha_by_tag.get(tag)))
            log.debug("resolved %s -> %s", tag, sha_by_tag.get(tag) or "UNKNOWN")

        token = gh.token

    if release is not None:
        known = {r["tag"] for r in normalized}
        if release not in known:
            raise SystemExit(
                f"Release {release!r} not found upstream. "
                f"Latest is {normalized[0]['tag']!r}."
            )

    storage = storage_from_env(dry_run=dry_run)
    already = collect_already_mirrored(storage, normalized)
    plan = plan_mirrors(
        releases=normalized,
        policy=policy,
        max_bytes=max_bytes,
        already_mirrored=already,
    )

    if release is not None:
        # Only upload this tag — keep full already_present so the index stays complete.
        before = len(plan.to_mirror)
        plan.to_mirror = [i for i in plan.to_mirror if i.tag == release]
        log.info(
            "scoped uploads to release %s: %s/%s asset(s) to mirror",
            release,
            len(plan.to_mirror),
            before,
        )

    index = build_releases_index(
        repo=repo,
        public_base=public_base,
        normalized_releases=normalized,
        plan=plan,
        max_bytes=max_bytes,
    )
    _print_plan(plan, max_bytes=max_bytes, dry_run=dry_run, index=index)

    if dry_run:
        if write_index:
            # Index must only mark assets already in R2 — not ones we merely planned.
            index_for_disk = build_releases_index(
                repo=repo,
                public_base=public_base,
                normalized_releases=normalized,
                plan=MirrorPlan(
                    to_mirror=[],
                    already_present=plan.already_present,
                    skipped_policy=plan.skipped_policy,
                    skipped_budget=plan.skipped_budget,
                ),
                max_bytes=max_bytes,
            )
            _write_index(None, index_for_disk, upload=False)
            log.info("dry-run wrote local index only (no R2 upload)")
        else:
            log.info("dry-run complete; nothing written")
        return 0

    sha_by_asset = execute_mirror_plan(storage, plan, token=token)

    # Rebuild index with real SHAs after uploads (failed assets drop out of to_mirror).
    index = build_releases_index(
        repo=repo,
        public_base=public_base,
        normalized_releases=normalized,
        plan=plan,
        max_bytes=max_bytes,
        sha256_by_asset=sha_by_asset,
    )
    _write_index(storage, index, upload=True)

    print("--- post-upload summary ---")
    print(f"  mirrored ok:  {len(plan.to_mirror)}")
    print(f"  failed:       {len(plan.failed)}")
    print(f"  sha256 known: {len(sha_by_asset)}")
    if release:
        print(f"  release:      {release}")
    print(f"  index:        {PUBLIC_INDEX}")
    print()

    if plan.failed:
        log.error("%s asset(s) failed — see log above", len(plan.failed))
        return 1
    return 0


def _print_plan(
    plan: MirrorPlan,
    *,
    max_bytes: int,
    dry_run: bool,
    index: dict[str, Any],
) -> None:
    mode = "DRY-RUN" if dry_run else "SYNC"
    print()
    print(f"=== {mode} mirror plan ===")
    print(f"releases in index: {len(index['releases'])}")
    print(f"would mirror:      {len(plan.to_mirror)} assets ({_fmt_bytes(plan.new_bytes)})")
    print(
        f"already present:   {len(plan.already_present)} assets "
        f"({_fmt_bytes(sum(i.size_bytes for i in plan.already_present))})"
    )
    print(f"skipped (policy):  {len(plan.skipped_policy)} assets")
    print(f"skipped (budget):  {len(plan.skipped_budget)} assets")
    print(f"projected total:   {_fmt_bytes(plan.projected_bytes)} / {_fmt_bytes(max_bytes)}")
    print(f"budget remaining:  {_fmt_bytes(max_bytes - plan.projected_bytes)}")
    print()

    if plan.to_mirror:
        print("--- assets to mirror (newest first) ---")
        for item in plan.to_mirror:
            print(
                f"  + {item.tag:16} {item.abi or 'unknown':12} "
                f"{_fmt_bytes(item.size_bytes):>10}  {item.filename}"
            )
        print()

    if plan.skipped_budget:
        print("--- skipped: byte budget ---")
        for item in plan.skipped_budget:
            print(
                f"  ! {item.tag:16} {item.abi or 'unknown':12} "
                f"{_fmt_bytes(item.size_bytes):>10}  {item.filename}"
            )
        print()

    if plan.skipped_policy:
        by_tag: dict[str, int] = {}
        bytes_by_tag: dict[str, int] = {}
        for item in plan.skipped_policy:
            by_tag[item.tag] = by_tag.get(item.tag, 0) + 1
            bytes_by_tag[item.tag] = bytes_by_tag.get(item.tag, 0) + item.size_bytes
        n_assets = len(plan.skipped_policy)
        n_rels = len(by_tag)
        print(f"--- skipped: policy ({n_assets} assets across {n_rels} releases) ---")
        for tag in list(by_tag)[:15]:
            print(
                f"  - {tag:16} {by_tag[tag]} asset(s) "
                f"({_fmt_bytes(bytes_by_tag[tag])})"
            )
        if len(by_tag) > 15:
            print(f"  ... and {len(by_tag) - 15} more releases")
        print()

    print("--- run summary ---")
    print(f"  total objects (projected): {len(plan.to_mirror) + len(plan.already_present)}")
    print(f"  total bytes (projected):   {_fmt_bytes(plan.projected_bytes)}")
    print(f"  bytes remaining in budget: {_fmt_bytes(max_bytes - plan.projected_bytes)}")
    print(f"  assets skipped (policy):   {len(plan.skipped_policy)}")
    print(f"  assets skipped (budget):   {len(plan.skipped_budget)}")
    print(f"  assets failed:             {len(plan.failed)}")
    print()


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Mirror bitchat-android APKs to R2")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan mirrors against GitHub without writing to R2",
    )
    parser.add_argument(
        "--write-index",
        action="store_true",
        help="With --dry-run, still write public/releases.json locally (no R2)",
    )
    parser.add_argument(
        "--release",
        metavar="TAG",
        help="Only upload assets for this release tag (index still covers all releases)",
    )
    args = parser.parse_args(argv)
    _configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
    return run(
        dry_run=args.dry_run,
        release=args.release,
        write_index=args.write_index,
    )


if __name__ == "__main__":
    sys.exit(main())
