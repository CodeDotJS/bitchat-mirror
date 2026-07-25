# Build prompt: bitchat-android release mirror (zero-cost architecture)

Paste this whole file into Claude Code as the initial prompt.

---

## Context

Build a self-hosted mirror and download site for the open-source Android app **bitchat**
(upstream: `https://github.com/permissionlesstech/bitchat-android`, GPLv3).

The site serves APKs that have been copied to object storage, not proxied from GitHub at request
time. A scheduled job keeps that copy in sync. Once an APK is mirrored, the site keeps serving it
whether or not GitHub is reachable.

**There is no server and no database.** The whole system is three pieces:

1. A Python script run by **GitHub Actions** on a cron schedule — mirrors APKs into R2 and
   regenerates a JSON index.
2. **Cloudflare R2** — holds the APKs. Free tier is 10 GB with zero egress fees.
3. A **static frontend on Vercel** — plain HTML/CSS/JS that reads the JSON index and links
   directly at R2.

Downloads go browser → R2. They never touch Vercel's bandwidth meter and never invoke a function.

Everything stays inside free tiers. Do not introduce anything that requires a paid plan, a
long-running process, a database, or a serverless function.

**Licensing requirement (not optional):** bitchat is GPLv3. Redistributing binaries obliges the
distributor to make the corresponding source available. Every release in the index must carry the
upstream git tag and the commit SHA it was built from, the frontend must link to that commit, and
the footer must state this is an unofficial mirror with a link upstream. Implement this.

---

## Repository layout

```
bitchat-mirror/
  .github/workflows/
    sync.yml
  sync/
    __init__.py
    main.py            # entry point
    github.py          # releases API client
    storage.py         # R2 (S3-compatible) client
    policy.py          # which assets to mirror, byte budget
    index.py           # builds releases.json
    models.py          # dataclasses
  public/              # <- Vercel serves this directory as static
    index.html
    archive.html
    style.css
    app.js
    releases.json      # generated, committed by the workflow
  tests/
    test_policy.py
    test_index.py
    test_github.py
    conftest.py
  requirements.txt
  vercel.json
  .env.example
  README.md
```

Python 3.11+, `httpx` for the GitHub API, `boto3` for R2's S3-compatible API, `pytest` + `respx`
for tests, `ruff` for lint. Pin versions.

---

## Part 1 — the sync job

### Trigger

`.github/workflows/sync.yml`: `schedule` cron every 6 hours, plus `workflow_dispatch` for manual
runs. Needs `permissions: contents: write` so it can commit the regenerated index.

Secrets: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`,
`R2_PUBLIC_BASE`. `GITHUB_TOKEN` is injected automatically and raises the API rate limit from 60
to 5000 requests/hour — use it.

The runner has roughly 14 GB of disk. Process **one asset at a time**: stream it to a temp file,
upload, delete, move on. Never accumulate.

### Steps

1. **Enumerate every release.** Paginate `GET /repos/{REPO}/releases?per_page=100&page=N` until an
   empty page. Roughly 40 releases across 4 pages today — don't hardcode a count. Keep prereleases
   in the index, flagged.
2. **Resolve each tag's commit SHA** via `GET /repos/{REPO}/git/ref/tags/{tag}`, dereferencing
   annotated tag objects. Required for GPL attribution.
3. **Classify each `.apk` asset by ABI** from its filename, first match wins:
   `arm64|aarch64` → `arm64-v8a`; `armeabi|armv7|arm32` → `armeabi-v7a`; `x86[_-]?64` → `x86_64`;
   `x86` → `x86`; `universal|all` → `universal`; else `null`. Ignore non-APK assets.
4. **Apply the mirror policy** (see below) to decide which assets to copy.
5. **Skip what's already there.** `HeadObject` on the target key; if it exists and its
   `Content-Length` matches the upstream asset size and its `x-amz-meta-sha256` is present, skip.
6. **Mirror the rest.** Stream from `browser_download_url` to a temp file while computing SHA-256,
   then `upload_file` to R2 with `ContentType: application/vnd.android.package-archive`,
   `Metadata: {"sha256": ..., "upstream-tag": ...}`, and a long `CacheControl` (these files are
   immutable). boto3 handles multipart automatically for large objects. Delete the temp file.
   Retry 3× with exponential backoff (2s/8s/32s); on final failure record the error and continue —
   one bad asset must not fail the run.
7. **Regenerate `public/releases.json`** and write it both to R2 (`index/releases.json`) and to the
   repo.
8. **Write per-release checksum files** to R2 at `checksums/{tag}.sha256` in `sha256sum -c` format.
9. **Commit** `public/releases.json` if it changed. This triggers Vercel's git integration to
   redeploy. Use a `[skip ci]`-free message so the deploy actually fires.

### Object key layout

```
apk/{tag}/{filename}
checksums/{tag}.sha256
index/releases.json
```

### Mirror policy

Configured by env, default `smart`:

- `smart` — mirror **every ABI** for releases `>= 1.6.0`, and only **arm64-v8a + universal** for
  anything older. Rationale: pre-1.6.0 builds bundled a large Tor library and each APK ran to
  ~150 MB, while 1.6.0+ switched to a self-compiled arti build at roughly a tenth the size. The
  old 32-bit and x86 variants are the least useful and the most expensive.
- `all` — everything, no filtering.
- `latest:N` — the N most recent stable releases only.

**Hard byte budget.** `MAX_BYTES` defaults to 9.5 GB, under R2's 10 GB free tier. Before each
upload, check projected total; if it would exceed the budget, stop mirroring, log a clear warning
naming what was skipped, and still write a complete index. Newest releases are processed first so
the budget is spent on what people actually want.

At the end of every run, log: total objects, total bytes, bytes remaining in budget, assets
skipped, assets failed.

### `releases.json` schema

The index describes **every upstream release**, mirrored or not. Unmirrored ones still appear,
marked so, with their upstream URL. Keep it a single file — at ~40 releases it's well under
100 KB.

```json
{
  "generated_at": "2026-07-24T09:00:00Z",
  "repo": "permissionlesstech/bitchat-android",
  "public_base": "https://<r2-public-host>",
  "budget": { "used_bytes": 0, "max_bytes": 10200547328 },
  "releases": [
    {
      "tag": "1.7.2",
      "name": "1.7.2",
      "published_at": "2026-03-30T08:45:00Z",
      "prerelease": false,
      "commit_sha": "4dfec917c822368a90bf0ae046e3cb354fbd6cd6",
      "upstream_url": "https://github.com/.../releases/tag/1.7.2",
      "notes_md": "...",
      "assets": [
        {
          "filename": "bitchat-1.7.2-arm64-v8a.apk",
          "abi": "arm64-v8a",
          "size_bytes": 15728640,
          "sha256": "...",
          "mirrored": true,
          "url": "https://<r2-public-host>/apk/1.7.2/bitchat-1.7.2-arm64-v8a.apk",
          "upstream_url": "https://github.com/.../bitchat-1.7.2-arm64-v8a.apk"
        }
      ]
    }
  ]
}
```

---

## Part 2 — the frontend

Static files in `public/`, deployed to Vercel. No framework, no build step, no bundler. Plain ES
modules and CSS. `vercel.json` sets `outputDirectory: "public"` and long cache headers on
`style.css` / `app.js`, short on `releases.json`.

The page fetches `/releases.json` from its own origin — fast, CDN-cached, and it means the index
loads even if R2 is slow. All download links point at the R2 public base URL from the index.

### Design direction

Carry over this token system — it exists already and is deliberate, so don't redesign it:

```css
--ink:#08191F;       /* page background */
--ink-2:#0E2A33;     /* grid lines, card fills */
--line:#17414C;      /* borders */
--paper:#DDE9E4;     /* body text */
--paper-dim:#8FA9A6; /* secondary text */
--signal:#5FD1B2;    /* primary accent, download buttons */
--hop:#F2A63B;       /* secondary accent, focus rings, alerts */
```

Type: `Archivo` (variable — use the `wdth` axis, condensed and heavy for display) for headings and
UI; `JetBrains Mono` for filenames, hashes, versions, and all data. 2px radius throughout. A 44px
CSS grid overlay on `--ink` as the background.

Voice: plain, active, specific. "Download", not "Get it now". Errors say what happened and what to
do next. Sentence case.

### `index.html` — latest release

- **Hero:** an animated canvas showing a BLE mesh. Nodes drift within a unit square, links draw
  between any pair closer than 0.30, and a packet travels a real BFS-computed path from a random
  source to the furthest reachable node, with a TTL counter decrementing per hop. This is the one
  bold element on the site — keep everything else quiet and disciplined. Honour
  `prefers-reduced-motion` by rendering a single static frame with no animation loop.
- **Latest release block:** tag, publish date, and one row per ABI with a human-readable size and a
  plain-English note ("Most phones from 2017 onward" for arm64-v8a, "Runs anywhere, larger file"
  for universal).
- Each row shows the first 16 hex chars of the SHA-256 with a copy button, and links to the full
  `checksums/{tag}.sha256`.
- **Sideloading section:** enabling install-from-unknown-sources for the browser, granting
  Bluetooth and Nearby devices, and why Android demands location permission for BLE scanning
  (a platform requirement for scanning, not the app asking for GPS).
- **Verify section:** compare the SHA-256 against what's shown, and note the source is public.
- **Footer:** unofficial mirror, links to the upstream repo, upstream releases, and the GPLv3 text.

### `archive.html` — every release

- Dense list of all releases, newest first: tag, date, asset count, total size, mirror state, and a
  link to the upstream tag.
- Filters: ABI, stable vs prerelease, and a text search across tag and release notes.
- Expanding a row reveals the release notes and the per-asset download rows.
  **Sanitize the markdown before rendering — it is untrusted upstream input.** Use a small
  client-side renderer with escaping, or pre-render sanitized HTML in the sync job.
- Releases that aren't mirrored render greyed with an "Upstream only" label and a direct GitHub
  link. Do not hide them — a complete index is the point.

### States

Every fetch has three rendered states: loading, empty, and error. The error state names the failure
and offers the upstream URL as a fallback. Time out at 10 seconds — no spinner that hangs forever.

---

## Configuration

`.env.example`, mirrored by the Actions secrets:

```
REPO=permissionlesstech/bitchat-android
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET=bitchat-mirror
R2_PUBLIC_BASE=https://pub-xxxxxxxx.r2.dev
MIRROR_POLICY=smart          # smart | all | latest:N
MAX_BYTES=10200547328        # 9.5 GiB
LOG_LEVEL=INFO
```

R2 endpoint is `https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com`, region `auto`.

---

## Non-negotiables

- **Stream, never buffer.** No asset is ever fully held in memory.
- **Never upload a partially downloaded file.** Verify the byte count before uploading.
- **Never fail the whole run because one asset failed.** Log it, mark it, continue.
- **Sanitize release notes before rendering.** Upstream markdown is untrusted.
- **Respect the byte budget.** Blowing past 10 GB turns a free project into a billed one.
- Structured logging with a clear per-run summary.
- The index must be complete even when the mirror is partial.

## Explicitly out of scope

Do not build: user accounts, download counters, a database, serverless functions, mirror-to-mirror
failover, domain rotation, proxy support, block detection, or transport obfuscation. This is a
plain archive.

---

## Tests

- `test_policy.py`: ABI classification across every pattern including the fallback; `smart` policy
  correctly splits at 1.6.0; byte budget stops at the right asset; version comparison handles
  `1.6.0-rc1` correctly (a prerelease sorts below `1.6.0`).
- `test_index.py`: unmirrored releases still appear in the index with upstream URLs; schema
  round-trips; `generated_at` is UTC ISO 8601.
- `test_github.py`: pagination across multiple pages; annotated-tag dereferencing; rate-limit
  header handling.
- Mock the GitHub API with `respx` and R2 with `moto` or a stub. No network access in the suite.

## Acceptance

1. `python -m sync.main --dry-run` prints exactly what it would mirror and the projected byte
   total, without writing anything.
2. A real run mirrors the latest release into R2, and `sha256sum` on a downloaded APK matches both
   the index and the checksums file.
3. `archive.html` lists every upstream release, including ones not mirrored.
4. With `REPO` pointed at a nonexistent repo, the sync job fails loudly but the deployed site keeps
   serving the last good index.
5. `pytest` passes, `ruff check` is clean.
6. `README.md` covers: creating the R2 bucket and enabling public access, generating R2 API
   credentials, setting the six GitHub secrets, the first manual `workflow_dispatch` run, deploying
   `public/` to Vercel, and the known caveats below.

## Known caveats to document in the README

- **Scheduled workflows get auto-disabled after 60 days of repository inactivity**, and commits made
  by `GITHUB_TOKEN` don't reliably reset that clock. Either push manually now and then or re-enable
  the workflow when GitHub emails about it.
- **The `r2.dev` public bucket URL is rate-limited by Cloudflare and not intended for production
  traffic.** It's fine for personal use. If this ever gets real traffic, attach a custom domain to
  the bucket — that requires a domain on Cloudflare, which is the one thing here that isn't free.
- **R2 may require a payment method on file** even to use the free tier. Verify when you enable it.
- Vercel's Hobby plan is **non-commercial use only**.

---

Work incrementally, and stop for review between stages:

1. `sync/` with `--dry-run` against the real GitHub API. **Show me the output before uploading
   anything.**
2. R2 upload path, run against a single release first.
3. `releases.json` generation and the workflow file.
4. Frontend last.