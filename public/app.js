/** Shared utilities for the bitchat mirror frontend. */

export const UPSTREAM_REPO = "permissionlesstech/bitchat-android";
export const UPSTREAM_URL = `https://github.com/${UPSTREAM_REPO}`;
export const UPSTREAM_RELEASES = `${UPSTREAM_URL}/releases`;
export const GPL_URL = "https://www.gnu.org/licenses/gpl-3.0.html";
export const FETCH_TIMEOUT_MS = 10_000;

export const ABI_NOTES = {
  "arm64-v8a": "Most phones from 2017 onward",
  "armeabi-v7a": "Older 32-bit phones",
  x86_64: "Emulators and some Chromebooks",
  x86: "Older emulators",
  universal: "Runs anywhere, larger file",
};

/**
 * @param {number} bytes
 * @returns {string}
 */
export function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  const units = ["B", "KiB", "MiB", "GiB"];
  let n = bytes;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i += 1;
  }
  const digits = i === 0 ? 0 : n >= 100 ? 0 : n >= 10 ? 1 : 2;
  return `${n.toFixed(digits)} ${units[i]}`;
}

/**
 * @param {string | null | undefined} iso
 * @returns {string}
 */
export function formatDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-GB", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/**
 * @param {string | null | undefined} sha
 * @returns {string}
 */
export function shortSha(sha) {
  if (!sha) return "";
  return sha.slice(0, 16);
}

/**
 * Escape text for safe HTML text nodes / attributes.
 * @param {string} value
 * @returns {string}
 */
export function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/**
 * Minimal markdown → sanitized HTML. Upstream notes are untrusted.
 * Supports: headings, paragraphs, lists, bold/italic, inline code, links.
 * @param {string} md
 * @returns {string}
 */
export function renderMarkdownSafe(md) {
  if (!md) return "<p><em>No release notes.</em></p>";

  const escaped = escapeHtml(md.replace(/\r\n/g, "\n"));
  const lines = escaped.split("\n");
  const out = [];
  let inList = false;

  const flushList = () => {
    if (inList) {
      out.push("</ul>");
      inList = false;
    }
  };

  const inline = (text) =>
    text
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\*([^*]+)\*/g, "<em>$1</em>")
      .replace(
        /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
        '<a href="$2" rel="noopener noreferrer">$1</a>',
      )
      .replace(
        /(https?:\/\/[^\s<]+)/g,
        '<a href="$1" rel="noopener noreferrer">$1</a>',
      );

  for (const raw of lines) {
    const line = raw.trimEnd();
    if (!line.trim()) {
      flushList();
      continue;
    }
    const heading = /^(#{1,3})\s+(.+)$/.exec(line);
    if (heading) {
      flushList();
      const level = heading[1].length;
      out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      continue;
    }
    const bullet = /^[-*]\s+(.+)$/.exec(line);
    if (bullet) {
      if (!inList) {
        out.push("<ul>");
        inList = true;
      }
      out.push(`<li>${inline(bullet[1])}</li>`);
      continue;
    }
    flushList();
    out.push(`<p>${inline(line)}</p>`);
  }
  flushList();
  return out.join("\n");
}

/**
 * Fetch releases.json with a hard timeout.
 * @returns {Promise<object>}
 */
export async function fetchIndex() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const res = await fetch("/releases.json", {
      signal: controller.signal,
      headers: { Accept: "application/json" },
    });
    if (!res.ok) {
      throw new Error(`Could not load releases.json (HTTP ${res.status}).`);
    }
    return await res.json();
  } catch (err) {
    if (err?.name === "AbortError") {
      throw new Error(
        "Timed out loading releases.json after 10 seconds. Check your connection or try upstream.",
      );
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Prefer newest stable release; fall back to newest overall.
 * @param {object} index
 * @returns {object | null}
 */
export function latestRelease(index) {
  const releases = index?.releases ?? [];
  return releases.find((r) => !r.prerelease) ?? releases[0] ?? null;
}

/**
 * @param {object} release
 * @param {string} publicBase
 * @returns {string}
 */
export function checksumUrl(release, publicBase) {
  const base = (publicBase || "").replace(/\/$/, "");
  return `${base}/checksums/${encodeURIComponent(release.tag)}.sha256`;
}

/**
 * @param {object} index
 * @param {object} release
 * @returns {string}
 */
export function commitUrl(index, release) {
  const repo = index.repo || UPSTREAM_REPO;
  if (!release.commit_sha) return UPSTREAM_URL;
  return `https://github.com/${repo}/commit/${release.commit_sha}`;
}

/**
 * @param {HTMLElement} root
 * @param {"loading"|"empty"|"error"} kind
 * @param {string} [detail]
 */
export function renderState(root, kind, detail = "") {
  if (kind === "loading") {
    root.innerHTML = `<div class="status" role="status"><p class="title">Loading releases</p><p>Fetching the mirror index…</p></div>`;
    return;
  }
  if (kind === "empty") {
    root.innerHTML = `<div class="status"><p class="title">No releases yet</p><p>The index is empty. Check again after the next sync, or download from <a href="${UPSTREAM_RELEASES}">upstream releases</a>.</p></div>`;
    return;
  }
  root.innerHTML = `<div class="status error" role="alert"><p class="title">Could not load the index</p><p>${escapeHtml(detail || "Unknown error.")}</p><p>Download from <a href="${UPSTREAM_RELEASES}">GitHub releases</a> instead.</p></div>`;
}

/**
 * Build one asset download row.
 * @param {object} asset
 * @param {object} release
 * @param {string} publicBase
 * @returns {string}
 */
export function assetRowHtml(asset, release, publicBase) {
  const mirrored = Boolean(asset.mirrored && asset.url);
  const href = mirrored ? asset.url : asset.upstream_url;
  const label = mirrored ? "Download" : "Upstream";
  const note = ABI_NOTES[asset.abi] || "ABI not detected from filename";
  const abi = asset.abi || "unknown";
  const sha = asset.sha256;
  const shaBits = sha
    ? `<div class="asset-hash">
        <span title="${escapeHtml(sha)}">${escapeHtml(shortSha(sha))}…</span>
        <button type="button" class="btn btn-ghost btn-tiny" data-copy="${escapeHtml(sha)}">Copy</button>
        <a class="btn btn-ghost btn-tiny" href="${escapeHtml(checksumUrl(release, publicBase))}">Checksums</a>
      </div>`
    : `<div class="asset-hash"><span>SHA-256 available after mirror sync</span>
        <a class="btn btn-ghost btn-tiny" href="${escapeHtml(checksumUrl(release, publicBase))}">Checksums</a>
      </div>`;

  return `<li class="asset-row${mirrored ? "" : " upstream-only"}">
    <div class="asset-main">
      <div class="asset-abi">
        <span class="abi">${escapeHtml(abi)}</span>
        <span class="size">${escapeHtml(formatBytes(asset.size_bytes))}</span>
        ${mirrored ? "" : '<span class="badge">Upstream only</span>'}
      </div>
      <p class="asset-note">${escapeHtml(note)}</p>
      <p class="asset-file">${escapeHtml(asset.filename)}</p>
      ${shaBits}
    </div>
    <div class="asset-actions">
      <a class="btn ${mirrored ? "btn-signal" : "btn-ghost"}" href="${escapeHtml(href)}" rel="noopener noreferrer">${label}</a>
    </div>
  </li>`;
}

/** Wire copy buttons inside a root. */
export function bindCopyButtons(root) {
  root.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("[data-copy]");
    if (!btn) return;
    const value = btn.getAttribute("data-copy");
    try {
      await navigator.clipboard.writeText(value);
      const prev = btn.textContent;
      btn.textContent = "Copied";
      setTimeout(() => {
        btn.textContent = prev;
      }, 1200);
    } catch {
      btn.textContent = "Failed";
    }
  });
}

export function footerHtml() {
  return `<footer class="site-footer wrap">
    <p>Unofficial mirror of <a href="${UPSTREAM_URL}">${UPSTREAM_REPO}</a>. Not affiliated with the upstream project.</p>
    <p>bitchat is <a href="${GPL_URL}">GPLv3</a>. Corresponding source is the tagged commit on each release.</p>
    <nav aria-label="Footer">
      <a href="${UPSTREAM_URL}">Upstream repo</a>
      <a href="${UPSTREAM_RELEASES}">Upstream releases</a>
      <a href="${GPL_URL}">GPLv3</a>
      <a href="/archive.html">Archive</a>
    </nav>
  </footer>`;
}
