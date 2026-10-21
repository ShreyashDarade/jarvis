// The holographic core: a particle sphere inside rotating arc rings. Pure canvas, no DOM or network.
// Cyan = listening, gold = speaking / working, red = stopped. Reacts to audio level.
const COLORS = {
  idle: [53, 208, 255], connecting: [53, 208, 255], listening: [53, 208, 255],
  speaking: [255, 176, 46], working: [255, 176, 46], stopped: [255, 107, 97],
};
const SPEED = { idle: 0.25, connecting: 1, listening: 0.7, speaking: 1.5, working: 2.4, stopped: 0 };
const ALPHA = { idle: 0.45, connecting: 0.8, listening: 0.9, speaking: 1, working: 1, stopped: 0.5 };

// Arc rings, from the outside in: radius (fraction of R), width, arcs [startFraction, lengthFraction], speed
const RINGS = [
  { r: 0.97, w: 1, arcs: [[0, 1]], dash: [2, 7], speed: 0.04 },
  { r: 0.90, w: 3, arcs: [[0.02, 0.18], [0.30, 0.10], [0.55, 0.22], [0.86, 0.08]], speed: -0.10 },
  { r: 0.82, w: 1.5, arcs: [[0, 1]], dash: [14, 5], speed: 0.07 },
  { r: 0.74, w: 5, arcs: [[0.10, 0.30], [0.58, 0.16]], speed: 0.18 },
  { r: 0.66, w: 2, arcs: [[0.00, 0.40], [0.50, 0.40]], speed: -0.26 },
];

function fibonacciSphere(n) {
  const pts = [];
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < n; i++) {
    const y = 1 - (i / (n - 1)) * 2;
    const r = Math.sqrt(1 - y * y);
    pts.push([Math.cos(golden * i) * r, y, Math.sin(golden * i) * r]);
  }
  return pts;
}

export class Hud {
  constructor(canvas, getLevel) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.getLevel = getLevel;
    this.state = "idle";
    this.color = [...COLORS.idle];
    this.alpha = ALPHA.idle;
    this.t = 0;
    this.level = 0;
    this.points = fibonacciSphere(420);
    this.reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
    new ResizeObserver(() => { this.resize(); if (this.reduced) this.draw(0); }).observe(canvas);
    this.resize();
  }

  resize() {
    const dpr = window.devicePixelRatio || 1;
    this.size = this.canvas.clientWidth;
    this.canvas.width = this.canvas.height = Math.round(this.size * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  setState(state) {
    this.state = state in COLORS ? state : "idle";
    if (this.reduced) { this.color = [...COLORS[this.state]]; this.alpha = ALPHA[this.state]; this.draw(0); }
  }

  start() {
    if (this.reduced) return this.draw(0);
    let last = performance.now();
    const frame = (now) => {
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      try { this.draw(dt); } finally { requestAnimationFrame(frame); }  // one bad frame must not stop the animation
    };
    requestAnimationFrame(frame);
  }

  draw(dt) {
    const { ctx, size } = this;
    if (size < 40) return;  // not laid out yet (or hidden)
    const speed = SPEED[this.state];
    this.t += dt * speed;
    // Ease toward the target colour, brightness and the live audio level.
    const target = COLORS[this.state];
    for (let i = 0; i < 3; i++) this.color[i] += (target[i] - this.color[i]) * 0.08;
    this.alpha += (ALPHA[this.state] - this.alpha) * 0.08;
    this.level += ((this.getLevel?.() ?? 0) - this.level) * 0.25;

    const [cr, cg, cb] = this.color.map(Math.round);
    const rgba = (a) => `rgba(${cr},${cg},${cb},${(a * this.alpha).toFixed(3)})`;
    const cx = size / 2, cy = size / 2, R = size / 2 - 4;
    const t = this.t, lvl = this.level;

    ctx.clearRect(0, 0, size, size);
    ctx.lineCap = "round";

    // Ambient glow
    const glow = ctx.createRadialGradient(cx, cy, R * 0.1, cx, cy, R);
    glow.addColorStop(0, rgba(0.18 + lvl * 0.25));
    glow.addColorStop(1, rgba(0));
    ctx.fillStyle = glow;
    ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.fill();

    // Tick ring
    ctx.strokeStyle = rgba(0.7);
    for (let i = 0; i < 90; i++) {
      const a = (i / 90) * Math.PI * 2 + t * 0.03;
      const long = i % 9 === 0;
      const r1 = R, r2 = R * (long ? 0.94 : 0.965);
      ctx.lineWidth = long ? 2 : 1;
      ctx.beginPath();
      ctx.moveTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1);
      ctx.lineTo(cx + Math.cos(a) * r2, cy + Math.sin(a) * r2);
      ctx.stroke();
    }

    // Arc rings
    for (const ring of RINGS) {
      ctx.lineWidth = ring.w;
      ctx.setLineDash(ring.dash ?? []);
      ctx.strokeStyle = rgba(ring.w > 2 ? 0.95 : 0.55);
      const spin = t * ring.speed * Math.PI * 2;
      for (const [start, len] of ring.arcs) {
        const a0 = start * Math.PI * 2 + spin;
        ctx.beginPath();
        ctx.arc(cx, cy, R * ring.r, a0, a0 + len * Math.PI * 2);
        ctx.stroke();
      }
    }
    ctx.setLineDash([]);

    // Orbital streaks (the swirling data trails)
    ctx.lineWidth = 1;
    for (let i = 0; i < 12; i++) {
      const tilt = (i / 12) * Math.PI + t * 0.05;
      const sweep = (t * (0.15 + (i % 4) * 0.05) + i * 0.7) % (Math.PI * 2);
      ctx.strokeStyle = rgba(0.35 + 0.4 * ((i % 3) / 3));
      ctx.beginPath();
      ctx.ellipse(cx, cy, R * 0.58, R * (0.18 + (i % 5) * 0.05), tilt, sweep, sweep + 1.1 + lvl * 1.2);
      ctx.stroke();
    }

    // Particle sphere
    const ry = t * 0.9, tiltX = 0.4;
    const cosY = Math.cos(ry), sinY = Math.sin(ry), cosX = Math.cos(tiltX), sinX = Math.sin(tiltX);
    const sphereR = R * 0.5 * (1 + lvl * 0.18);
    for (let i = 0; i < this.points.length; i++) {
      const [px, py, pz] = this.points[i];
      const wob = 1 + lvl * 0.2 * Math.sin(i * 12.9898 + t * 14);
      let x = px * wob, y = py * wob, z = pz * wob;
      const x1 = x * cosY + z * sinY, z1 = -x * sinY + z * cosY;
      const y2 = y * cosX - z1 * sinX, z2 = y * sinX + z1 * cosX;
      const persp = 1 / (1 - z2 * 0.35);
      const depth = (z2 + 1) / 2;
      ctx.fillStyle = rgba(0.2 + 0.8 * depth);
      const s = 0.8 + depth * 1.6;
      ctx.fillRect(cx + x1 * sphereR * persp - s / 2, cy + y2 * sphereR * persp - s / 2, s, s);
    }

    // Core
    const coreR = R * (0.12 + lvl * 0.08);
    const core = ctx.createRadialGradient(cx, cy, 0, cx, cy, coreR * 2.2);
    core.addColorStop(0, `rgba(255,255,255,${(0.9 * this.alpha).toFixed(3)})`);
    core.addColorStop(0.25, rgba(0.9));
    core.addColorStop(1, rgba(0));
    ctx.fillStyle = core;
    ctx.beginPath(); ctx.arc(cx, cy, coreR * 2.2, 0, Math.PI * 2); ctx.fill();
  }
}
