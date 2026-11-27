<script setup lang="ts">
import { ref, watch, onMounted, onBeforeUnmount } from "vue";
import { useThemeStore } from "@/stores/theme";

const canvasRef = ref<HTMLCanvasElement | null>(null);
const theme = useThemeStore();

let rafId: number | null = null;
let reducedMotion = false;
let width = 0;
let height = 0;
let time = 1.6;

const pointer = {
  x: 0.5,
  y: 0.44,
  targetX: 0.5,
  targetY: 0.44,
  active: false,
  intensity: 0,
};

interface AuroraOrb {
  baseX: number;
  baseY: number;
  ampX: number;
  ampY: number;
  freqX: number;
  freqY: number;
  phase: number;
  pull: number;
  radiusRatio: number;
  lightStops: [string, string];
  darkStops: [string, string];
}

interface ComputeNode {
  x: number;
  y: number;
  vx: number;
  vy: number;
  size: number;
  pulsePhase: number;
  tone: 0 | 1 | 2;
}

interface SilkRibbon {
  yRatio: number;
  amp: number;
  freq: number;
  speed: number;
  phase: number;
  thickness: number;
  tone: 0 | 1 | 2;
}

const auroraOrbs: AuroraOrb[] = [
  {
    baseX: 0.2,
    baseY: 0.24,
    ampX: 0.14,
    ampY: 0.12,
    freqX: 0.26,
    freqY: 0.21,
    phase: 0.3,
    pull: 0.2,
    radiusRatio: 0.46,
    lightStops: ["rgba(209, 98, 58, 0.30)", "rgba(235, 162, 108, 0.12)"],
    darkStops: ["rgba(224, 114, 74, 0.34)", "rgba(196, 86, 56, 0.10)"],
  },
  {
    baseX: 0.78,
    baseY: 0.22,
    ampX: 0.13,
    ampY: 0.14,
    freqX: 0.22,
    freqY: 0.29,
    phase: 1.9,
    pull: -0.16,
    radiusRatio: 0.48,
    lightStops: ["rgba(232, 168, 92, 0.30)", "rgba(244, 206, 146, 0.12)"],
    darkStops: ["rgba(218, 152, 74, 0.24)", "rgba(148, 92, 46, 0.08)"],
  },
  {
    baseX: 0.76,
    baseY: 0.78,
    ampX: 0.15,
    ampY: 0.11,
    freqX: 0.19,
    freqY: 0.25,
    phase: 3.4,
    pull: 0.22,
    radiusRatio: 0.5,
    lightStops: ["rgba(68, 128, 178, 0.24)", "rgba(126, 174, 212, 0.09)"],
    darkStops: ["rgba(86, 144, 208, 0.28)", "rgba(56, 98, 152, 0.09)"],
  },
  {
    baseX: 0.24,
    baseY: 0.8,
    ampX: 0.12,
    ampY: 0.12,
    freqX: 0.28,
    freqY: 0.17,
    phase: 4.8,
    pull: -0.18,
    radiusRatio: 0.44,
    lightStops: ["rgba(56, 142, 114, 0.22)", "rgba(124, 186, 162, 0.08)"],
    darkStops: ["rgba(68, 164, 134, 0.25)", "rgba(42, 108, 92, 0.08)"],
  },
  {
    baseX: 0.5,
    baseY: 0.5,
    ampX: 0.1,
    ampY: 0.09,
    freqX: 0.24,
    freqY: 0.2,
    phase: 2.6,
    pull: 0.28,
    radiusRatio: 0.38,
    lightStops: ["rgba(186, 112, 158, 0.18)", "rgba(218, 156, 132, 0.07)"],
    darkStops: ["rgba(166, 102, 182, 0.22)", "rgba(217, 119, 87, 0.08)"],
  },
];

const ribbons: SilkRibbon[] = [
  { yRatio: 0.24, amp: 44, freq: 0.0024, speed: 0.65, phase: 0.4, thickness: 1.5, tone: 0 },
  { yRatio: 0.36, amp: 58, freq: 0.0019, speed: -0.5, phase: 1.7, thickness: 1.2, tone: 1 },
  { yRatio: 0.52, amp: 52, freq: 0.0022, speed: 0.55, phase: 3.1, thickness: 1.6, tone: 0 },
  { yRatio: 0.68, amp: 46, freq: 0.0026, speed: -0.6, phase: 4.4, thickness: 1.15, tone: 2 },
  { yRatio: 0.81, amp: 38, freq: 0.0021, speed: 0.48, phase: 5.5, thickness: 1.0, tone: 1 },
];

let nodes: ComputeNode[] = [];

function seedNodes() {
  const count = Math.max(26, Math.min(54, Math.round((width * height) / 34000)));
  const seeded: ComputeNode[] = [];
  for (let i = 0; i < count; i++) {
    // Deterministic golden-ratio spread so first frame looks balanced immediately
    const u = ((i * 0.61803398875) % 1 + 1) % 1;
    const v = ((i * 0.38196601125 + 0.17 * Math.sin(i * 1.7)) % 1 + 1) % 1;
    const angle = i * 2.3999632297;
    const speed = 0.18 + (i % 5) * 0.07;
    seeded.push({
      x: u * width,
      y: v * height,
      vx: Math.cos(angle) * speed,
      vy: Math.sin(angle) * speed,
      size: 1.5 + (i % 4) * 0.55,
      pulsePhase: i * 0.9,
      tone: (i % 3) as 0 | 1 | 2,
    });
  }
  nodes = seeded;
}

function resize() {
  const canvas = canvasRef.value;
  if (!canvas) return;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  width = window.innerWidth;
  height = window.innerHeight;
  canvas.width = Math.floor(width * dpr);
  canvas.height = Math.floor(height * dpr);
  const ctx = canvas.getContext("2d");
  if (ctx) ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  seedNodes();
  drawFrame();
}

function drawAurora(ctx: CanvasRenderingContext2D, isDark: boolean) {
  const maxDim = Math.max(width, height);
  const dx = pointer.x - 0.5;
  const dy = pointer.y - 0.5;

  // Base editorial gradient backdrop
  const baseGrad = ctx.createLinearGradient(0, 0, width, height);
  if (isDark) {
    baseGrad.addColorStop(0, "#141413");
    baseGrad.addColorStop(0.5, "#1b1a18");
    baseGrad.addColorStop(1, "#161920");
  } else {
    baseGrad.addColorStop(0, "#f7f4ec");
    baseGrad.addColorStop(0.52, "#f3efe4");
    baseGrad.addColorStop(1, "#eceae2");
  }
  ctx.fillStyle = baseGrad;
  ctx.fillRect(0, 0, width, height);

  ctx.globalCompositeOperation = isDark ? "screen" : "multiply";
  for (const orb of auroraOrbs) {
    const cx = (orb.baseX + Math.sin(time * orb.freqX + orb.phase) * orb.ampX + dx * orb.pull) * width;
    const cy = (orb.baseY + Math.cos(time * orb.freqY + orb.phase) * orb.ampY + dy * orb.pull) * height;
    const radius = Math.max(260, maxDim * orb.radiusRatio);
    const stops = isDark ? orb.darkStops : orb.lightStops;
    const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius);
    grad.addColorStop(0, stops[0]);
    grad.addColorStop(0.5, stops[1]);
    grad.addColorStop(1, "rgba(0, 0, 0, 0)");
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.globalCompositeOperation = "source-over";
}

function drawDotGrid(ctx: CanvasRenderingContext2D, isDark: boolean) {
  const spacing = 36;
  const px = pointer.x * width;
  const py = pointer.y * height;
  const influence = Math.min(width, height) * 0.42;

  for (let x = spacing / 2; x < width; x += spacing) {
    for (let y = spacing / 2; y < height; y += spacing) {
      const dist = Math.hypot(x - px, y - py);
      const proximity = Math.max(0, 1 - dist / influence);
      const baseAlpha = isDark ? 0.055 : 0.065;
      const alpha = baseAlpha + proximity * proximity * (isDark ? 0.24 : 0.22);
      const r = 0.85 + proximity * 0.9;
      ctx.fillStyle =
        proximity > 0.25
          ? isDark
            ? `rgba(224, 134, 96, ${alpha.toFixed(3)})`
            : `rgba(184, 83, 50, ${alpha.toFixed(3)})`
          : isDark
            ? `rgba(242, 240, 233, ${alpha.toFixed(3)})`
            : `rgba(61, 61, 58, ${alpha.toFixed(3)})`;
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fill();
    }
  }
}

function drawSilkRibbons(ctx: CanvasRenderingContext2D, isDark: boolean) {
  const px = pointer.x * width;
  const py = pointer.y * height;
  const step = Math.max(16, Math.floor(width / 80));

  const strokes = isDark
    ? [
        ["rgba(217, 119, 87, 0)", "rgba(217, 119, 87, 0.34)", "rgba(235, 168, 102, 0.28)", "rgba(217, 119, 87, 0)"],
        ["rgba(127, 169, 212, 0)", "rgba(127, 169, 212, 0.28)", "rgba(109, 192, 156, 0.24)", "rgba(127, 169, 212, 0)"],
        ["rgba(176, 132, 206, 0)", "rgba(176, 132, 206, 0.24)", "rgba(217, 119, 87, 0.22)", "rgba(176, 132, 206, 0)"],
      ]
    : [
        ["rgba(184, 83, 50, 0)", "rgba(184, 83, 50, 0.26)", "rgba(212, 138, 66, 0.22)", "rgba(184, 83, 50, 0)"],
        ["rgba(62, 103, 144, 0)", "rgba(62, 103, 144, 0.22)", "rgba(41, 107, 79, 0.20)", "rgba(62, 103, 144, 0)"],
        ["rgba(148, 86, 134, 0)", "rgba(148, 86, 134, 0.18)", "rgba(184, 83, 50, 0.20)", "rgba(148, 86, 134, 0)"],
      ];

  for (const ribbon of ribbons) {
    const palette = strokes[ribbon.tone];
    const lineGrad = ctx.createLinearGradient(0, 0, width, 0);
    lineGrad.addColorStop(0, palette[0]);
    lineGrad.addColorStop(0.28, palette[1]);
    lineGrad.addColorStop(0.72, palette[2]);
    lineGrad.addColorStop(1, palette[3]);

    // Draw a subtle dual-strand harmonic ribbon
    for (let strand = 0; strand < 2; strand++) {
      const offsetY = strand === 0 ? 0 : 12;
      const phaseShift = strand === 0 ? 0 : 0.65;
      ctx.beginPath();
      for (let x = -step; x <= width + step; x += step) {
        const baseY = ribbon.yRatio * height + offsetY;
        const primaryWave = Math.sin(x * ribbon.freq + time * ribbon.speed + ribbon.phase + phaseShift) * ribbon.amp;
        const harmonic =
          Math.cos(x * ribbon.freq * 1.85 - time * ribbon.speed * 0.7 + ribbon.phase) * (ribbon.amp * 0.34);
        let y = baseY + primaryWave + harmonic;

        // Gentle vertical wave ripple near cursor
        const dist = Math.hypot(x - px, y - py);
        if (dist < 260) {
          const factor = Math.cos((dist / 260) * (Math.PI / 2));
          const push = (y < py ? -1 : 1) * factor * factor * (22 + pointer.intensity * 16);
          y += push;
        }
        if (x === -step) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }

      ctx.strokeStyle = lineGrad;
      ctx.lineWidth = strand === 0 ? ribbon.thickness : ribbon.thickness * 0.6;
      ctx.stroke();
    }
  }
}

function drawConstellation(ctx: CanvasRenderingContext2D, isDark: boolean) {
  const px = pointer.x * width;
  const py = pointer.y * height;
  const maxLinkDist = 148;
  const cursorLinkDist = 195;

  // Update node motion when animating
  if (!reducedMotion) {
    for (const node of nodes) {
      const dx = px - node.x;
      const dy = py - node.y;
      const dist = Math.hypot(dx, dy);
      if (pointer.active && dist > 28 && dist < 210) {
        // Gentle orbital pull toward cursor
        node.vx += (dx / dist) * 0.008;
        node.vy += (dy / dist) * 0.008;
      }
      // Speed clamp & gentle damping
      const speed = Math.hypot(node.vx, node.vy);
      if (speed > 0.75) {
        node.vx = (node.vx / speed) * 0.75;
        node.vy = (node.vy / speed) * 0.75;
      }
      node.x += node.vx;
      node.y += node.vy;

      if (node.x < -20) node.x = width + 20;
      else if (node.x > width + 20) node.x = -20;
      if (node.y < -20) node.y = height + 20;
      else if (node.y > height + 20) node.y = -20;
    }
  }

  // Connecting filaments between nearby nodes
  for (let i = 0; i < nodes.length; i++) {
    const a = nodes[i];
    for (let j = i + 1; j < nodes.length; j++) {
      const b = nodes[j];
      const dist = Math.hypot(a.x - b.x, a.y - b.y);
      if (dist >= maxLinkDist) continue;
      const alpha = (1 - dist / maxLinkDist) * (isDark ? 0.22 : 0.16);
      ctx.strokeStyle = isDark
        ? `rgba(225, 168, 134, ${alpha.toFixed(3)})`
        : `rgba(148, 86, 58, ${alpha.toFixed(3)})`;
      ctx.lineWidth = 0.9;
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    }

    // Filaments linking cursor to nearby nodes
    const cursorDist = Math.hypot(a.x - px, a.y - py);
    if (cursorDist < cursorLinkDist) {
      const alpha = (1 - cursorDist / cursorLinkDist) * (isDark ? 0.38 : 0.28) * (0.45 + pointer.intensity * 0.55);
      ctx.strokeStyle = isDark
        ? `rgba(217, 119, 87, ${alpha.toFixed(3)})`
        : `rgba(184, 83, 50, ${alpha.toFixed(3)})`;
      ctx.lineWidth = 1.05;
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(px, py);
      ctx.stroke();
    }
  }

  // Glowing compute nodes
  const nodeTonesDark = ["rgba(226, 132, 96, ", "rgba(134, 182, 224, ", "rgba(116, 196, 164, "];
  const nodeTonesLight = ["rgba(184, 83, 50, ", "rgba(62, 103, 144, ", "rgba(41, 107, 79, "];
  const tones = isDark ? nodeTonesDark : nodeTonesLight;

  for (const node of nodes) {
    const pulse = 0.7 + 0.3 * Math.sin(time * 2.2 + node.pulsePhase);
    const cursorBoost = Math.max(0, 1 - Math.hypot(node.x - px, node.y - py) / 200);
    const alpha = Math.min(0.9, (isDark ? 0.45 : 0.38) * pulse + cursorBoost * 0.45);
    const radius = node.size + cursorBoost * 1.4;

    // Soft outer glow halo
    if (radius > 2.2) {
      const halo = ctx.createRadialGradient(node.x, node.y, 0, node.x, node.y, radius * 4.5);
      halo.addColorStop(0, `${tones[node.tone]}${(alpha * 0.45).toFixed(3)})`);
      halo.addColorStop(1, `${tones[node.tone]}0)`);
      ctx.fillStyle = halo;
      ctx.beginPath();
      ctx.arc(node.x, node.y, radius * 4.5, 0, Math.PI * 2);
      ctx.fill();
    }

    ctx.fillStyle = `${tones[node.tone]}${alpha.toFixed(3)})`;
    ctx.beginPath();
    ctx.arc(node.x, node.y, radius, 0, Math.PI * 2);
    ctx.fill();
  }
}

function drawCursorSpotlight(ctx: CanvasRenderingContext2D, isDark: boolean) {
  const px = pointer.x * width;
  const py = pointer.y * height;
  const radius = Math.max(220, Math.min(width, height) * 0.34);
  ctx.globalCompositeOperation = isDark ? "screen" : "source-over";
  const grad = ctx.createRadialGradient(px, py, 0, px, py, radius);
  if (isDark) {
    grad.addColorStop(0, `rgba(226, 130, 92, ${(0.18 + pointer.intensity * 0.08).toFixed(3)})`);
    grad.addColorStop(0.45, "rgba(126, 166, 214, 0.06)");
    grad.addColorStop(1, "rgba(0, 0, 0, 0)");
  } else {
    grad.addColorStop(0, `rgba(202, 102, 64, ${(0.14 + pointer.intensity * 0.07).toFixed(3)})`);
    grad.addColorStop(0.5, "rgba(232, 174, 114, 0.06)");
    grad.addColorStop(1, "rgba(0, 0, 0, 0)");
  }
  ctx.fillStyle = grad;
  ctx.beginPath();
  ctx.arc(px, py, radius, 0, Math.PI * 2);
  ctx.fill();
  ctx.globalCompositeOperation = "source-over";
}

function drawFrame() {
  const canvas = canvasRef.value;
  if (!canvas || !width || !height) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  const isDark = theme.resolvedTheme === "dark";
  ctx.clearRect(0, 0, width, height);

  drawAurora(ctx, isDark);
  drawDotGrid(ctx, isDark);
  drawSilkRibbons(ctx, isDark);
  drawConstellation(ctx, isDark);
  drawCursorSpotlight(ctx, isDark);
}

function tick() {
  if (reducedMotion || document.visibilityState !== "visible") {
    rafId = null;
    return;
  }
  time += 0.011;
  pointer.x += (pointer.targetX - pointer.x) * 0.065;
  pointer.y += (pointer.targetY - pointer.y) * 0.065;
  pointer.intensity += ((pointer.active ? 1 : 0) - pointer.intensity) * 0.04;
  drawFrame();
  rafId = window.requestAnimationFrame(tick);
}

function startLoop() {
  if (rafId !== null || reducedMotion || document.visibilityState !== "visible") return;
  rafId = window.requestAnimationFrame(tick);
}

function stopLoop() {
  if (rafId !== null) {
    window.cancelAnimationFrame(rafId);
    rafId = null;
  }
}

function onPointerMove(event: PointerEvent) {
  if (!width || !height) return;
  pointer.active = true;
  pointer.targetX = Math.max(0, Math.min(1, event.clientX / width));
  pointer.targetY = Math.max(0, Math.min(1, event.clientY / height));
}

function onPointerLeave() {
  pointer.active = false;
}

function onVisibilityChange() {
  if (document.visibilityState === "visible") startLoop();
  else stopLoop();
}

watch(
  () => theme.resolvedTheme,
  () => drawFrame(),
);

onMounted(() => {
  const mq = window.matchMedia?.("(prefers-reduced-motion: reduce)");
  reducedMotion = Boolean(mq?.matches);
  resize();
  window.addEventListener("resize", resize);
  window.addEventListener("pointermove", onPointerMove, { passive: true });
  document.addEventListener("pointerleave", onPointerLeave);
  document.addEventListener("visibilitychange", onVisibilityChange);
  startLoop();
});

onBeforeUnmount(() => {
  stopLoop();
  window.removeEventListener("resize", resize);
  window.removeEventListener("pointermove", onPointerMove);
  document.removeEventListener("pointerleave", onPointerLeave);
  document.removeEventListener("visibilitychange", onVisibilityChange);
});
</script>

<template>
  <canvas
    ref="canvasRef"
    aria-hidden="true"
    class="pointer-events-none fixed inset-0 z-0 h-dvh w-dvw"
  />
</template>
