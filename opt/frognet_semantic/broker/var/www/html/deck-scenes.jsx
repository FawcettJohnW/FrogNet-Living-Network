// Embeddable animated scenes for the Living Network deck.
// Each is a React component that fills its parent (a 1920×1080 deck slide).
// Raw requestAnimationFrame; no engine dependency.

const PAPER = '#f3efe4', INK = '#1b1a16', GREEN = '#2c5a3c', AMBER = '#b4663c', MUTE = '#6f6d64';

function useCanvas(draw) {
  const ref = React.useRef(null);
  React.useEffect(() => {
    const cv = ref.current; if (!cv) return;
    const ctx = cv.getContext('2d');
    let raf, t0 = performance.now(), last = t0, st = {};
    const loop = (now) => {
      const dt = Math.min(0.05, (now - last) / 1000); last = now;
      try { draw(ctx, (now - t0) / 1000, dt, st); } catch (e) { console.error(e); return; }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, []);
  return ref;
}

// ───────────────────────── SWARM (hero, slide 1) ─────────────────────────
function SwarmScene() {
  const cx = 1320, cy = 540, areaR = 430, R = 300, N = 12;
  const st = React.useRef(null);
  const ref = useCanvas((ctx, t, dt, S) => {
    if (!st.current) {
      const v = [];
      for (let i = 0; i < N; i++) {
        const a = Math.random() * 6.2832, rr = areaR * 0.85 * Math.sqrt(Math.random());
        v.push({ x: cx + Math.cos(a) * rr, y: cy + Math.sin(a) * rr,
          h: Math.random() * 6.2832, sp: 30 + Math.random() * 22,
          wx: cx + (Math.random() - 0.5) * areaR, wy: cy + (Math.random() - 0.5) * areaR });
      }
      st.current = { v, brain: 0, pulses: [], cmd: 1, nextHand: 11, hand: null };
    }
    const S2 = st.current, v = S2.v;
    // move
    for (const n of v) {
      let dx = n.wx - n.x, dy = n.wy - n.y, d = Math.hypot(dx, dy);
      if (d < 50) { const a = Math.random() * 6.2832, rr = areaR * Math.sqrt(Math.random());
        n.wx = cx + Math.cos(a) * rr; n.wy = cy + Math.sin(a) * rr; }
      let da = Math.atan2(dy, dx) - n.h; while (da > Math.PI) da -= 6.2832; while (da < -Math.PI) da += 6.2832;
      const cd = Math.hypot(n.x - cx, n.y - cy);
      if (cd > areaR * 0.98) { let ia = Math.atan2(cy - n.y, cx - n.x) - n.h; while (ia > Math.PI) ia -= 6.2832; while (ia < -Math.PI) ia += 6.2832; da = ia; }
      const turn = 2.4 * dt; n.h += Math.max(-turn, Math.min(turn, da));
      n.x += Math.cos(n.h) * n.sp * dt; n.y += Math.sin(n.h) * n.sp * dt;
    }
    // graph (BFS tree from brain)
    const edges = [], adj = v.map(() => []);
    for (let i = 0; i < N; i++) for (let j = i + 1; j < N; j++) {
      const d = Math.hypot(v[i].x - v[j].x, v[i].y - v[j].y);
      if (d < R) { edges.push([i, j, 1 - d / R]); adj[i].push(j); adj[j].push(i); } }
    const depth = new Array(N).fill(-1), par = new Array(N).fill(-1), conn = new Array(N).fill(false);
    depth[S2.brain] = 0; conn[S2.brain] = true; const q = [S2.brain];
    while (q.length) { const u = q.shift(); for (const w of adj[u]) if (depth[w] < 0) { depth[w] = depth[u] + 1; par[w] = u; conn[w] = true; q.push(w); } }
    // command pulses
    S2.cmd -= dt; if (S2.cmd <= 0) { S2.cmd = 2.3;
      for (let k = 0; k < N; k++) if (k !== S2.brain && conn[k]) S2.pulses.push({ from: par[k], to: k, t0: depth[par[k]] * 0.14, dur: 0.6, age: 0 }); }
    for (const p of S2.pulses) p.age += dt;
    S2.pulses = S2.pulses.filter(p => p.age <= p.t0 + p.dur);
    // handoff
    S2.nextHand -= dt;
    if (!S2.hand && S2.nextHand <= 0) { let best = -1, bs = -1;
      for (let k = 0; k < N; k++) { if (k === S2.brain || !conn[k]) continue;
        const sc = adj[k].length * 1000 - Math.hypot(v[k].x - cx, v[k].y - cy); if (sc > bs) { bs = sc; best = k; } }
      if (best >= 0) S2.hand = { from: S2.brain, to: best, t: 0 }; else S2.nextHand = 3; }
    if (S2.hand) { S2.hand.t += dt / 1.4; if (S2.hand.t >= 1) { S2.brain = S2.hand.to; S2.hand = null; S2.nextHand = 11; } }

    // ---- draw ----
    ctx.clearRect(0, 0, 1920, 1080);
    ctx.strokeStyle = 'rgba(150,138,116,0.10)'; ctx.lineWidth = 1;
    for (let x = 0; x <= 1920; x += 80) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, 1080); ctx.stroke(); }
    for (let y = 0; y <= 1080; y += 80) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(1920, y); ctx.stroke(); }
    ctx.save(); ctx.setLineDash([10, 11]); ctx.strokeStyle = 'rgba(44,90,60,0.26)'; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(cx, cy, areaR, 0, 6.2832); ctx.stroke(); ctx.restore();
    for (const e of edges) { ctx.strokeStyle = 'rgba(44,90,60,' + (0.08 + 0.18 * e[2]) + ')'; ctx.lineWidth = 1 + e[2];
      ctx.beginPath(); ctx.moveTo(v[e[0]].x, v[e[0]].y); ctx.lineTo(v[e[1]].x, v[e[1]].y); ctx.stroke(); }
    for (let k = 0; k < N; k++) { if (k === S2.brain || !conn[k]) continue;
      ctx.strokeStyle = 'rgba(44,90,60,0.42)'; ctx.lineWidth = 2.2; ctx.beginPath(); ctx.moveTo(v[par[k]].x, v[par[k]].y); ctx.lineTo(v[k].x, v[k].y); ctx.stroke(); }
    for (const p of S2.pulses) { const f = (p.age - p.t0) / p.dur; if (f < 0 || f > 1) continue;
      const a = v[p.from], b = v[p.to], x = a.x + (b.x - a.x) * f, y = a.y + (b.y - a.y) * f;
      ctx.fillStyle = 'rgba(44,90,60,0.22)'; ctx.beginPath(); ctx.arc(x, y, 9, 0, 6.2832); ctx.fill();
      ctx.fillStyle = GREEN; ctx.beginPath(); ctx.arc(x, y, 5, 0, 6.2832); ctx.fill(); }
    for (let k = 0; k < N; k++) {
      const n = v[k], isB = k === S2.brain;
      if (isB) { const rr = 26 + 4 * Math.sin(t * 3);
        ctx.strokeStyle = 'rgba(44,90,60,0.5)'; ctx.lineWidth = 2.2; ctx.beginPath(); ctx.arc(n.x, n.y, rr, 0, 6.2832); ctx.stroke();
        ctx.fillStyle = GREEN; ctx.font = "700 15px 'Archivo',sans-serif"; ctx.textAlign = 'center'; ctx.textBaseline = 'bottom'; ctx.fillText('AI', n.x, n.y - rr - 6); }
      ctx.save(); ctx.translate(n.x, n.y); ctx.rotate(n.h); const sz = isB ? 16 : 12;
      ctx.beginPath(); ctx.moveTo(sz, 0); ctx.lineTo(-sz * 0.72, sz * 0.72); ctx.lineTo(-sz * 0.36, 0); ctx.lineTo(-sz * 0.72, -sz * 0.72); ctx.closePath();
      ctx.fillStyle = isB ? GREEN : INK; ctx.fill(); ctx.restore();
    }
    if (S2.hand) { const a = v[S2.hand.from], b = v[S2.hand.to], f = S2.hand.t, x = a.x + (b.x - a.x) * f, y = a.y + (b.y - a.y) * f;
      ctx.fillStyle = 'rgba(44,90,60,0.15)'; ctx.beginPath(); ctx.arc(x, y, 24, 0, 6.2832); ctx.fill();
      ctx.fillStyle = GREEN; ctx.beginPath(); ctx.arc(x, y, 11, 0, 6.2832); ctx.fill();
      ctx.fillStyle = PAPER; ctx.font = "700 12px 'Archivo',sans-serif"; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText('AI', x, y); }
  });
  return React.createElement('canvas', { ref, width: 1920, height: 1080,
    style: { position: 'absolute', inset: 0, width: '100%', height: '100%' } });
}

// ───────────────────────── LIVING MEMORY (slide 4) ─────────────────────────
function MemoryScene() {
  const mx = 960, my = 470, r = 150, N = 7;
  const sensors = [];
  for (let i = 0; i < N; i++) { const a = -Math.PI / 2 + i * 2 * Math.PI / N;
    sensors.push({ x: mx + Math.cos(a) * 320, y: my + Math.sin(a) * 250, a }); }
  const labels = ['TEMP', 'RANGE', 'VIDEO', 'HEADING', 'BATTERY', 'LIDAR', 'AUDIO'];
  const ref = useCanvas((ctx, t) => {
    ctx.clearRect(0, 0, 1920, 1080);
    // glow
    const pulse = 0.5 + 0.5 * Math.sin(t * 2.2);
    const g = ctx.createRadialGradient(mx, my, 12, mx, my, r + 40);
    g.addColorStop(0, 'rgba(44,90,60,' + (0.30 + 0.06 * pulse) + ')'); g.addColorStop(1, 'rgba(44,90,60,0)');
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(mx, my, r + 40, 0, 6.2832); ctx.fill();
    // streams
    for (let i = 0; i < N; i++) { const s = sensors[i];
      const d = Math.hypot(s.x - mx, s.y - my), ex = mx + (s.x - mx) * (r / d), ey = my + (s.y - my) * (r / d);
      ctx.strokeStyle = 'rgba(44,90,60,0.28)'; ctx.lineWidth = 1.6; ctx.beginPath(); ctx.moveTo(s.x, s.y); ctx.lineTo(ex, ey); ctx.stroke();
      for (let k = 0; k < 3; k++) { const f = ((t * 0.6 + i * 0.17 + k / 3) % 1), x = s.x + (ex - s.x) * f, y = s.y + (ey - s.y) * f;
        ctx.fillStyle = 'rgba(44,90,60,' + (0.7 * (1 - f) + 0.25) + ')'; ctx.beginPath(); ctx.arc(x, y, 3.4, 0, 6.2832); ctx.fill(); } }
    // memory core
    ctx.fillStyle = 'rgba(44,90,60,0.10)'; ctx.beginPath(); ctx.arc(mx, my, r, 0, 6.2832); ctx.fill();
    ctx.strokeStyle = 'rgba(44,90,60,0.55)'; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(mx, my, r, 0, 6.2832); ctx.stroke();
    ctx.fillStyle = GREEN; ctx.textAlign = 'center'; ctx.font = "800 24px 'Archivo',sans-serif"; ctx.textBaseline = 'middle';
    ctx.fillText('SHARED', mx, my - 16); ctx.fillText('MEMORY', mx, my + 14);
    ctx.font = "500 14px 'IBM Plex Mono',monospace"; ctx.fillStyle = 'rgba(44,90,60,0.7)';
    ctx.fillText('read the space · don\u2019t message the node', mx, my + 42);
    // sensors
    for (let i = 0; i < N; i++) { const s = sensors[i];
      ctx.fillStyle = GREEN; ctx.beginPath(); ctx.arc(s.x, s.y, 13, 0, 6.2832); ctx.fill();
      ctx.strokeStyle = 'rgba(44,90,60,0.25)'; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(s.x, s.y, 16 + 2 * Math.sin(t * 3 + i), 0, 6.2832); ctx.stroke();
      ctx.fillStyle = INK; ctx.font = "500 15px 'IBM Plex Mono',monospace"; ctx.textBaseline = 'middle';
      const left = s.x < mx; ctx.textAlign = left ? 'right' : 'left'; ctx.fillText(labels[i], s.x + (left ? -20 : 20), s.y); }
  });
  return React.createElement('canvas', { ref, width: 1920, height: 1080,
    style: { position: 'absolute', inset: 0, width: '100%', height: '100%' } });
}

// ───────────────────── INTERLOCK WEB (ambient, slide 3) ─────────────────────
function InterlockWeb() {
  const st = React.useRef(null);
  const ref = useCanvas((ctx, t, dt, S) => {
    if (!st.current) {
      const pts = [];
      const M = 26;
      for (let i = 0; i < M; i++) pts.push({
        x: Math.random() * 1920, y: Math.random() * 1080,
        vx: (Math.random() - 0.5) * 16, vy: (Math.random() - 0.5) * 16 });
      st.current = { pts, sparks: [], spk: 0 };
    }
    const S2 = st.current, pts = S2.pts;
    for (const p of pts) { p.x += p.vx * dt; p.y += p.vy * dt;
      if (p.x < 0 || p.x > 1920) p.vx *= -1; if (p.y < 0 || p.y > 1080) p.vy *= -1; }
    ctx.clearRect(0, 0, 1920, 1080);
    const LINK = 360;
    const near = [];
    for (let i = 0; i < pts.length; i++) for (let j = i + 1; j < pts.length; j++) {
      const dx = pts[i].x - pts[j].x, dy = pts[i].y - pts[j].y, d = Math.hypot(dx, dy);
      if (d < LINK) { const a = (1 - d / LINK) * 0.16; ctx.strokeStyle = 'rgba(44,90,60,' + a + ')'; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(pts[i].x, pts[i].y); ctx.lineTo(pts[j].x, pts[j].y); ctx.stroke(); near.push([i, j]); } }
    for (const p of pts) { ctx.fillStyle = 'rgba(44,90,60,0.22)'; ctx.beginPath(); ctx.arc(p.x, p.y, 2.4, 0, 6.2832); ctx.fill(); }
    // traveling sparks along random links
    S2.spk -= dt; if (S2.spk <= 0 && near.length) { S2.spk = 0.32; const e = near[(Math.random() * near.length) | 0]; S2.sparks.push({ a: e[0], b: e[1], t: 0 }); }
    for (const s of S2.sparks) s.t += dt / 0.9;
    S2.sparks = S2.sparks.filter(s => s.t <= 1);
    for (const s of S2.sparks) { const a = pts[s.a], b = pts[s.b], x = a.x + (b.x - a.x) * s.t, y = a.y + (b.y - a.y) * s.t;
      ctx.fillStyle = 'rgba(44,90,60,' + (0.5 * (1 - s.t) + 0.2) + ')'; ctx.beginPath(); ctx.arc(x, y, 3.2, 0, 6.2832); ctx.fill(); }
  });
  return React.createElement('canvas', { ref, width: 1920, height: 1080,
    style: { position: 'absolute', inset: 0, width: '100%', height: '100%' } });
}

window.SwarmScene = SwarmScene;
window.MemoryScene = MemoryScene;
window.InterlockWeb = InterlockWeb;
