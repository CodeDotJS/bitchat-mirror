/**
 * BLE mesh visualization — phones talking peer-to-peer.
 * Lattice + jitter, proximity links, BFS-routed packet with TTL.
 */

/**
 * @param {HTMLCanvasElement} canvas
 * @param {{ caption?: HTMLElement | null }} [opts]
 */
export function startMesh(canvas, opts = {}) {
  const caption = opts.caption ?? null;
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const parent = canvas.parentElement;

  const COLS = 5;
  const ROWS = 4;
  const LINK_DIST = 0.34;
  const HOP_MS = 820;

  /** @type {{ x: number, y: number, ox: number, oy: number, phase: number }[]} */
  const nodes = [];
  for (let r = 0; r < ROWS; r += 1) {
    for (let c = 0; c < COLS; c += 1) {
      const ox = (c + 0.5) / COLS + (Math.random() - 0.5) * 0.06;
      const oy = (r + 0.5) / ROWS + (Math.random() - 0.5) * 0.08;
      nodes.push({
        x: ox,
        y: oy,
        ox,
        oy,
        phase: Math.random() * Math.PI * 2,
      });
    }
  }

  /** @type {{ path: number[], hop: number, ttl: number, hopStarted: number } | null} */
  let packet = null;
  let raf = 0;
  let running = true;

  function neighbors(i, positions) {
    const out = [];
    for (let j = 0; j < positions.length; j += 1) {
      if (i === j) continue;
      const dx = positions[i].x - positions[j].x;
      const dy = positions[i].y - positions[j].y;
      if (Math.hypot(dx, dy) < LINK_DIST) out.push(j);
    }
    return out;
  }

  function livePositions(t) {
    return nodes.map((n) => ({
      x: n.ox + Math.sin(t * 0.00055 + n.phase) * 0.018,
      y: n.oy + Math.cos(t * 0.00045 + n.phase * 1.3) * 0.016,
    }));
  }

  function bfsPath(src, positions) {
    const dist = Array(positions.length).fill(Infinity);
    const prev = Array(positions.length).fill(-1);
    const q = [src];
    dist[src] = 0;
    while (q.length) {
      const u = q.shift();
      for (const v of neighbors(u, positions)) {
        if (dist[v] <= dist[u] + 1) continue;
        dist[v] = dist[u] + 1;
        prev[v] = u;
        q.push(v);
      }
    }
    let dest = src;
    let best = 0;
    for (let i = 0; i < dist.length; i += 1) {
      if (Number.isFinite(dist[i]) && dist[i] > best) {
        best = dist[i];
        dest = i;
      }
    }
    if (dest === src || best < 2) return null;
    const path = [];
    for (let cur = dest; cur !== -1; cur = prev[cur]) path.push(cur);
    path.reverse();
    return path;
  }

  function spawnPacket(t, positions) {
    for (let attempt = 0; attempt < 8; attempt += 1) {
      const src = Math.floor(Math.random() * positions.length);
      const path = bfsPath(src, positions);
      if (path && path.length >= 3) {
        packet = {
          path,
          hop: 0,
          ttl: path.length - 1,
          hopStarted: t,
        };
        return;
      }
    }
    packet = null;
  }

  function resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const cssW = parent?.clientWidth || canvas.clientWidth || 640;
    const cssH = parent?.clientHeight || canvas.clientHeight || 320;
    canvas.width = Math.floor(cssW * dpr);
    canvas.height = Math.floor(Math.max(cssH, 200) * dpr);
  }

  function draw(t) {
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = canvas.width / dpr;
    const h = canvas.height / dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, w, h);

    // faint graph paper
    ctx.strokeStyle = "rgba(12, 20, 25, 0.05)";
    ctx.lineWidth = 1;
    const grid = 28;
    for (let x = 0; x < w; x += grid) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, h);
      ctx.stroke();
    }
    for (let y = 0; y < h; y += grid) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(w, y);
      ctx.stroke();
    }

    const padX = Math.max(28, w * 0.08);
    const padY = Math.max(24, h * 0.12);
    const sx = (x) => padX + x * (w - padX * 2);
    const sy = (y) => padY + y * (h - padY * 2);

    const positions = livePositions(t);
    const onPath = new Set(packet?.path ?? []);

    // links
    for (let i = 0; i < positions.length; i += 1) {
      for (let j = i + 1; j < positions.length; j += 1) {
        const dx = positions[i].x - positions[j].x;
        const dy = positions[i].y - positions[j].y;
        const d = Math.hypot(dx, dy);
        if (d >= LINK_DIST) continue;
        const active = onPath.has(i) && onPath.has(j);
        const alpha = 1 - d / LINK_DIST;
        ctx.strokeStyle = active
          ? `rgba(15, 122, 104, ${0.35 + alpha * 0.45})`
          : `rgba(12, 20, 25, ${0.08 + alpha * 0.18})`;
        ctx.lineWidth = active ? 1.75 : 1.1;
        ctx.beginPath();
        ctx.moveTo(sx(positions[i].x), sy(positions[i].y));
        ctx.lineTo(sx(positions[j].x), sy(positions[j].y));
        ctx.stroke();
      }
    }

    // nodes
    for (let i = 0; i < positions.length; i += 1) {
      const active = onPath.has(i);
      const x = sx(positions[i].x);
      const y = sy(positions[i].y);
      ctx.beginPath();
      ctx.arc(x, y, active ? 5 : 3.5, 0, Math.PI * 2);
      ctx.fillStyle = active ? "#0f7a68" : "#2a3a42";
      ctx.fill();
      if (active) {
        ctx.beginPath();
        ctx.arc(x, y, 9, 0, Math.PI * 2);
        ctx.strokeStyle = "rgba(15, 122, 104, 0.28)";
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }
    }

    // packet
    if (packet) {
      const progress = Math.min(1, (t - packet.hopStarted) / HOP_MS);
      const eased = progress * progress * (3 - 2 * progress);
      const a = packet.path[packet.hop];
      const bIdx = Math.min(packet.hop + 1, packet.path.length - 1);
      const b = packet.path[bIdx];
      const x = positions[a].x + (positions[b].x - positions[a].x) * eased;
      const y = positions[a].y + (positions[b].y - positions[a].y) * eased;
      const px = sx(x);
      const py = sy(y);

      ctx.beginPath();
      ctx.arc(px, py, 12, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(194, 65, 12, 0.15)";
      ctx.fill();

      ctx.beginPath();
      ctx.arc(px, py, 5.5, 0, Math.PI * 2);
      ctx.fillStyle = "#c2410c";
      ctx.fill();

      if (caption) {
        caption.textContent = `packet · ttl ${packet.ttl} · hop ${packet.hop}/${packet.path.length - 1}`;
      }

      if (progress >= 1) {
        if (packet.hop < packet.path.length - 1) {
          packet.hop += 1;
          packet.ttl = Math.max(0, packet.ttl - 1);
          packet.hopStarted = t;
        } else {
          packet = null;
        }
      }
    } else if (caption) {
      caption.textContent = "ble mesh · peer to peer";
    }

    return positions;
  }

  function frame(t) {
    if (!running) return;
    const positions = draw(t);
    if (!packet) spawnPacket(t, positions);
    raf = requestAnimationFrame(frame);
  }

  resize();
  const ro = new ResizeObserver(() => {
    resize();
    draw(performance.now());
  });
  if (parent) ro.observe(parent);

  if (reduceMotion) {
    const t = performance.now();
    const positions = livePositions(t);
    spawnPacket(t, positions);
    if (packet) {
      packet.hop = Math.max(0, Math.floor(packet.path.length / 2) - 1);
      packet.ttl = Math.max(0, packet.path.length - 1 - packet.hop);
      packet.hopStarted = t - HOP_MS * 0.55;
    }
    draw(t);
    if (caption) caption.textContent = "ble mesh · peer to peer";
    return () => {
      running = false;
      ro.disconnect();
    };
  }

  raf = requestAnimationFrame(frame);
  return () => {
    running = false;
    cancelAnimationFrame(raf);
    ro.disconnect();
  };
}
