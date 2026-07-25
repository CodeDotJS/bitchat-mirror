# Research — bitchat mirror / Why page

This folder is the **source-of-truth backup** for claims on `/why.html`.

The public Why page is not a product pitch for bitchat. It is a **sourced brief on Indian internet censorship and state entitlement over communication** — shutdowns, intermediary compulsion, and the July 2026 strike on offline messaging distribution. The mirror is the *consequence* of that record, not the subject of it.

## Layout

| Path | Purpose |
|---|---|
| `README.md` | This file — how to use the dossier |
| `THESIS.md` | Editorial thesis and what “Why” must do |
| `CLAIMS.md` | Every on-page claim → source ID → quote/figure |
| `SOURCES.md` | Canonical bibliography (stable IDs `S01`…) |
| `TIMELINE.md` | Dated events with citations |
| `sources/manifest.json` | Machine-readable URL → archived file map |
| `sources/raw/` | Archived HTML (via ScrapingBee or direct fetch) |
| `sources/text/` | Extracted plaintext / notes for offline reading |
| `scripts/archive_sources.py` | Re-fetch / refresh archives |

## Rules

1. **No unsourced numbers on the Why page.** If it is not in `CLAIMS.md` with a source ID, it does not ship.
2. **Prefer primary documents** (Access Now PDFs, Freedom House country page, SFLC tracker, court materials, government notices) over tertiary blogs.
3. **Archive before we depend.** When a URL matters to the page, run the archiver so `sources/raw/` holds a copy.
4. **Never commit secrets.** `.env` (including `SCRAPINGBEE_API_KEY`) stays gitignored. Archives must not embed API keys in filenames or metadata logs committed to git.
5. **Quote accurately.** Paraphrase carefully; when we use a number, match the source’s definition (e.g. Access Now “shutdown” ≠ every local outage Top10VPN counts).

## Refresh archives

```bash
# from repo root — uses SCRAPINGBEE_API_KEY from .env
./research/scripts/archive_sources.py
# or dry-run:
./research/scripts/archive_sources.py --dry-run
```

Direct PDF downloads (no ScrapingBee needed) are also recorded in `sources/manifest.json`.

## Relationship to the site

- `public/why.html` — user-facing narrative (must stay aligned with `CLAIMS.md`)
- `research/` — evidence locker for humans and agents
