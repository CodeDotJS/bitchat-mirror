# Deployment guide

This project has **no long-running app server**. Deploy in two pieces:

| Piece | What it is | Where it runs |
|---|---|---|
| **Backend (mirror job)** | Python sync that pulls APKs from GitHub and uploads them to object storage | GitHub Actions + Cloudflare R2 |
| **Frontend (site)** | Static files in `public/` | Vercel |

```
GitHub Actions (cron / manual)
        │
        ▼
   sync/ (Python)  ──►  Cloudflare R2   (APKs + checksums)
        │
        └──► commits public/releases.json
                    │
                    ▼
              Vercel redeploy  (static UI)
                    │
                    └──► browser downloads APKs from R2 (not Vercel)
```

Do the **backend first** (R2 + secrets + first sync), then the **frontend**. The site needs a real `releases.json` and a working `R2_PUBLIC_BASE` to show downloadable APKs.

---

## Prerequisites

- [ ] GitHub repository for this project (push `main`)
- [ ] Cloudflare account with **R2** enabled (payment method may be required even on free tier)
- [ ] Vercel account (Hobby is fine for non-commercial use)
- [ ] Locally: Python 3.11+ (3.12 recommended) for a smoke-test sync before going live

---

## Part A — Backend (R2 + GitHub Actions)

### A1. Create the R2 bucket

1. Open the [Cloudflare dashboard](https://dash.cloudflare.com/) → **R2**.
2. **Create bucket** — name it e.g. `bitchat-mirror`.
3. Enable **public access** / public development URL on the bucket.
4. Copy the public URL (`https://pub-….r2.dev`). This is `R2_PUBLIC_BASE`.

Object layout the sync job writes:

```
apk/{tag}/{filename}
checksums/{tag}.sha256
index/releases.json
```

### A2. Create an R2 API token

1. R2 → **Manage R2 API Tokens** → **Create API token**.
2. Permissions: **Object Read & Write** on your bucket.
3. Save:
   - Access Key ID → `R2_ACCESS_KEY_ID`
   - Secret Access Key → `R2_SECRET_ACCESS_KEY`
4. Account ID (R2 overview / sidebar) → `R2_ACCOUNT_ID`.

Endpoint used by sync: `https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com`.

### A3. (Optional) Smoke-test sync locally

```bash
cd bitchat-mirror
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -r requirements.txt

cp .env.example .env
# Fill R2_* and GITHUB_TOKEN (recommended — anonymous GitHub API is 60 req/h)
```

Dry plan (no uploads):

```bash
python -m sync.main --dry-run
# or: ./sync.sh --dry-run
```

First live upload (one release only):

```bash
python -m sync.main --release 1.7.4
```

Full policy sync (`MIRROR_POLICY=smart` by default):

```bash
python -m sync.main
```

Confirm an APK URL under `R2_PUBLIC_BASE` opens in the browser and its SHA-256 matches `checksums/{tag}.sha256`.

### A4. Add GitHub Actions secrets

In the GitHub repo: **Settings → Secrets and variables → Actions → New repository secret**.

| Secret | Value |
|---|---|
| `R2_ACCOUNT_ID` | Cloudflare account ID |
| `R2_ACCESS_KEY_ID` | R2 API token access key |
| `R2_SECRET_ACCESS_KEY` | R2 API token secret |
| `R2_BUCKET` | Bucket name (e.g. `bitchat-mirror`) |
| `R2_PUBLIC_BASE` | `https://pub-….r2.dev` (no trailing slash) |

Do **not** add `GITHUB_TOKEN` as a secret — Actions injects it automatically.

### A5. Run the sync workflow

1. Push this repo to GitHub (if you have not already).
2. **Actions → Sync bitchat-android releases → Run workflow**.
3. Recommended first run:
   - `release` = a recent tag (e.g. `1.7.4`) so you do not upload the whole archive at once
   - or leave blank for full `smart` policy
4. On success the workflow commits `public/releases.json` when it changed (`chore: update mirrored release index`) and pushes — that commit is what Vercel will pick up.

Schedule after that: weekly on **Sundays 06:00 UTC** (`0 6 * * 0`), plus manual `workflow_dispatch`.

### A6. Backend checklist

- [ ] Bucket public URL serves an APK
- [ ] Checksum file exists under `checksums/`
- [ ] Actions secrets are set
- [ ] Manual workflow run succeeded
- [ ] `public/releases.json` was committed (or already current)

---

## Part B — Frontend (Vercel)

### B1. Import the project

1. Open [Vercel](https://vercel.com/) → **Add New… → Project**.
2. Import the GitHub repository.
3. Configure:
   - **Framework Preset:** Other (no build)
   - **Root Directory:** `.` (repo root)
   - **Build Command:** leave empty
   - **Output Directory:** `public` (also set in `vercel.json`)
4. Deploy.

No environment variables are required on Vercel for the static site. Download links come from `releases.json` (`public_base` / per-asset R2 URLs written by the sync job).

### B2. Verify the site

After deploy, open the Vercel URL and check:

| URL | Expect |
|---|---|
| `/` | Latest release, APK buttons, mesh visual |
| `/archive.html` | Full release list + filters |
| `/why.html` | Censorship brief |
| `/releases.json` | JSON index (short cache) |
| An APK link | Hits `R2_PUBLIC_BASE`, not `*.vercel.app` |

### B3. How updates roll out

1. Actions sync runs (schedule or manual).
2. Sync uploads new APKs to R2 and rewrites `public/releases.json`.
3. Workflow commits + pushes the index when it changed.
4. Vercel git integration redeploys the static site.
5. Browsers fetch fresh `/releases.json` (cached ~60s per `vercel.json`).

### B4. Frontend checklist

- [ ] Vercel project linked to `main`
- [ ] Output directory is `public`
- [ ] Homepage shows a release (not empty / error)
- [ ] APK download host is R2 (`pub-….r2.dev` or your custom domain)
- [ ] `/why.html` and `/archive.html` load

---

## Recommended order (first-time go-live)

1. Create R2 bucket + public URL + API token (**A1–A2**).
2. Optional local smoke sync (**A3**).
3. Push repo; set Actions secrets (**A4**).
4. Run Actions sync for one release, then full sync (**A5**).
5. Import to Vercel and deploy (**B1–B2**).
6. Trigger another sync (or wait for cron) and confirm Vercel redeploys with the new index.

---

## Ongoing operations

| Task | How |
|---|---|
| Refresh APKs | Wait for cron, or **Actions → Run workflow** |
| Mirror one tag only | Workflow input `release=1.x.y`, or `python -m sync.main --release 1.x.y` |
| Plan without uploading | Workflow `dry_run=true`, or `python -m sync.main --dry-run` |
| Redeploy UI only | Push a commit to `main`, or **Vercel → Redeploy** |
| Rotate R2 keys | Create new API token → update GitHub secrets → delete old token |

---

## Custom domain (optional)

### Site (Vercel)

Vercel project → **Settings → Domains** → add your domain and follow DNS instructions.

### APK downloads (R2)

`r2.dev` URLs are rate-limited and fine for light use. For heavier traffic, attach a custom domain to the R2 bucket in Cloudflare (domain must be on Cloudflare). Then:

1. Update `R2_PUBLIC_BASE` to the custom origin (local `.env` + GitHub secret).
2. Re-run sync so `releases.json` points at the new base.
3. Confirm Vercel picks up the committed index.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Site shows empty / error on latest | No `releases.json` or sync never ran | Run Actions sync; confirm file is on `main` |
| APK link 404 | Wrong `R2_PUBLIC_BASE` or object not uploaded | Check secret, bucket public access, re-run sync |
| Actions fails on GitHub API | Rare with `GITHUB_TOKEN`; local without token burns 60 req/h | Set `GITHUB_TOKEN` locally |
| Actions fails on R2 auth | Bad keys / wrong account ID | Recreate API token; re-check secrets |
| Vercel did not update after sync | Commit did not land / project not watching `main` | Check Actions “Commit public/releases.json” step; confirm Vercel git integration |
| Scheduled sync stopped | GitHub disables cron after ~60 days inactivity | Re-enable the workflow; push a manual commit occasionally |
| Cursor terminal: `No module named 'boto3'` | AppImage Python shim | Use `./sync.sh` or an external terminal |

---

## Security notes

- Never commit `.env` or R2 secrets.
- Prefer GitHub Actions secrets over baking keys into Vercel (Vercel does not need them).
- `research/` and `overview.md` are gitignored — keep them local; they are not part of deploy.
- Vercel Hobby is for **non-commercial** use only.

---

## Acceptance (production)

- [ ] `python -m sync.main --dry-run` (or Actions dry run) plans without writing to R2
- [ ] Live sync mirrored at least the latest release; SHA-256 matches
- [ ] `/archive.html` lists releases; mirrored rows download from R2
- [ ] Bad sync leaves the last good `releases.json` serving
- [ ] Cron or manual Actions run completes green
- [ ] Vercel serves `/`, `/archive.html`, `/why.html`
