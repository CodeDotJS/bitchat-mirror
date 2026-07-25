#!/usr/bin/env python3
"""Archive Why-page sources into research/sources/raw/.

Uses SCRAPINGBEE_API_KEY from the environment or repo .env when set;
falls back to a plain HTTPS GET (adequate for many PDFs and open pages).

Usage:
  ./research/scripts/archive_sources.py
  ./research/scripts/archive_sources.py --dry-run
  ./research/scripts/archive_sources.py --only S05,S11
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "research" / "sources" / "raw"
TEXT = ROOT / "research" / "sources" / "text"
MANIFEST = ROOT / "research" / "sources" / "manifest.json"
ENV_FILE = ROOT / ".env"

# Keep in sync with research/SOURCES.md
SOURCES: list[dict[str, str]] = [
    {
        "id": "S01",
        "file": "S01-indianexpress-github-bitchat.html",
        "url": "https://indianexpress.com/article/business/india-orders-github-block-jack-dorsey-bitchat-app-delhi-protests-10801857/",
    },
    {
        "id": "S02",
        "file": "S02-toi-github-order.html",
        "url": "https://timesofindia.indiatimes.com/business/india-business/govt-orders-github-to-remove-code-for-internet-free-messaging-app/articleshow/132617808.cms",
    },
    {
        "id": "S03",
        "file": "S03-medianama-mha-order.txt",
        "url": "https://www.medianama.com/wp-content/uploads/2026/07/MHA-github-takedown-order.txt",
    },
    {
        "id": "S04",
        "file": "S04-indianexpress-keepiton-2025.html",
        "url": "https://indianexpress.com/article/india/india-recorded-65-internet-shutdowns-in-2025-highest-among-democracies-access-now-report-10613113/",
    },
    {
        "id": "S05",
        "file": "S05-keepiton-2025.pdf",
        "url": "https://www.accessnow.org/wp-content/uploads/2026/03/KeepItOn-Internet-Shutdowns-2025-Annual-Report.pdf",
    },
    {
        "id": "S06",
        "file": "S06-keepiton-2024.pdf",
        "url": "https://www.accessnow.org/wp-content/uploads/2025/02/KeepItOn-2024-Internet-Shutdowns-Annual-Report.pdf",
    },
    {
        "id": "S07",
        "file": "S07-sflc-jk.html",
        "url": "https://internetshutdowns.in/static-page/jammu-kashmir/",
    },
    {
        "id": "S08",
        "file": "S08-lkyspp-shutdowns.pdf",
        "url": "https://lkyspp.nus.edu.sg/docs/cases/internetshutdownindia_pubmgt_ind_nil_en_202606.pdf",
    },
    {
        "id": "S09",
        "file": "S09-accessnow-2023-india.html",
        "url": "https://www.accessnow.org/press-release/india-keepiton-internet-shutdowns-2023-en/",
    },
    {
        "id": "S10",
        "file": "S10-top10vpn-2023.html",
        "url": "https://www.top10vpn.com/research/cost-of-internet-shutdowns/2023/",
    },
    {
        "id": "S10b",
        "file": "S10b-medianama-585m.html",
        "url": "https://www.medianama.com/2024/01/223-top10vpn-india-internet-shutdown-2023-cost/",
    },
    {
        "id": "S11",
        "file": "S11-freedomhouse-india-2025.html",
        "url": "https://freedomhouse.org/country/india/freedom-net/2025",
    },
    {
        "id": "S12",
        "file": "S12-anuradha-bhasin.html",
        "url": "https://www.advocatekhoj.com/library/judgments/announcement.php?WID=12491",
    },
    {
        "id": "S13",
        "file": "S13-ie-sahyog-explained.html",
        "url": "https://indianexpress.com/article/explained/sahyog-x-censorship-portal-9916595/",
    },
    {
        "id": "S14",
        "file": "S14-ie-sahyog-rti.html",
        "url": "https://indianexpress.com/article/express-exclusive/130-censorship-orders-issued-via-homes-sahyog-portal-in-5-months-9957698/",
    },
    {
        "id": "S15",
        "file": "S15-ie-sahyog-hc.html",
        "url": "https://indianexpress.com/article/explained/explained-law/karnataka-hc-sahyog-portal-x-challenge-10269277/",
    },
    {
        "id": "S16",
        "file": "S16-bitchat-readme.html",
        "url": "https://github.com/permissionlesstech/bitchat",
    },
    {
        "id": "S17",
        "file": "S17-verge-bitchat.html",
        "url": "https://www.theverge.com/news/701272/jack-dorsey-bitchat-bluetooth-messaging-app",
    },
    {
        "id": "S18",
        "file": "S18-icrier-blackout.pdf",
        "url": "https://icrier.org/pdf/Anatomy_of_an_Internet_Blackout.pdf",
    },
    {
        "id": "S19",
        "file": "S19-sflc-home.html",
        "url": "https://internetshutdowns.in/",
    },
    {
        "id": "S20",
        "file": "S20-isoc-manipur-200.html",
        "url": "https://pulse.internetsociety.org/en/news/2023/11/internet-shutdown-in-manipur-has-now-crossed-200-days/",
    },
]


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


def fetch_direct(url: str, timeout: int = 90) -> tuple[bytes, str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "bitchat-mirror-research-archiver/1.0 (+local research backup)",
            "Accept": "*/*",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        ctype = resp.headers.get("Content-Type", "application/octet-stream")
        return data, ctype


def fetch_scrapingbee(url: str, api_key: str, timeout: int = 120) -> tuple[bytes, str]:
    params = urllib.parse.urlencode(
        {
            "api_key": api_key,
            "url": url,
            "render_js": "false",
            "premium_proxy": "true",
            "country_code": "in",
        }
    )
    endpoint = f"https://app.scrapingbee.com/api/v1/?{params}"
    req = urllib.request.Request(
        endpoint,
        headers={"User-Agent": "bitchat-mirror-research-archiver/1.0"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        ctype = resp.headers.get("Content-Type", "text/html")
        return data, ctype


def archive_one(entry: dict[str, str], api_key: str | None, dry_run: bool) -> dict:
    out = RAW / entry["file"]
    record: dict = {
        "id": entry["id"],
        "url": entry["url"],
        "file": entry["file"],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    if dry_run:
        record["status"] = "dry-run"
        print(f"[dry-run] {entry['id']} → {out.name}")
        return record

    methods = []
    if api_key and not entry["file"].endswith(".pdf"):
        methods.append("scrapingbee")
    methods.append("direct")

    last_err: Exception | None = None
    for method in methods:
        try:
            if method == "scrapingbee":
                assert api_key
                data, ctype = fetch_scrapingbee(entry["url"], api_key)
            else:
                data, ctype = fetch_direct(entry["url"])
            out.write_bytes(data)
            record["status"] = "ok"
            record["method"] = method
            record["bytes"] = len(data)
            record["content_type"] = ctype.split(";")[0].strip()
            print(f"[ok:{method}] {entry['id']} ({len(data)} bytes) → {out.name}")
            return record
        except Exception as exc:  # noqa: BLE001 — archive best-effort
            last_err = exc
            print(f"[fail:{method}] {entry['id']}: {exc}", file=sys.stderr)
            time.sleep(0.4)

    record["status"] = "error"
    record["error"] = str(last_err) if last_err else "unknown"
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only", default="", help="Comma-separated source IDs")
    args = parser.parse_args()

    load_dotenv(ENV_FILE)
    api_key = os.environ.get("SCRAPINGBEE_API_KEY", "").strip() or None
    if api_key:
        print("ScrapingBee: enabled")
    else:
        print("ScrapingBee: disabled (set SCRAPINGBEE_API_KEY in .env for blocked pages)")

    RAW.mkdir(parents=True, exist_ok=True)
    TEXT.mkdir(parents=True, exist_ok=True)

    wanted = {x.strip() for x in args.only.split(",") if x.strip()}
    entries = [e for e in SOURCES if not wanted or e["id"] in wanted]

    results = []
    for entry in entries:
        results.append(archive_one(entry, api_key, args.dry_run))
        if not args.dry_run:
            time.sleep(0.6)

    if not args.dry_run:
        MANIFEST.write_text(
            json.dumps(
                {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "scrapingbee": bool(api_key),
                    "sources": results,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {MANIFEST.relative_to(ROOT)}")

    errors = [r for r in results if r.get("status") == "error"]
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
