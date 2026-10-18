import { useEffect, useRef } from 'react';
import type { ChartPoint } from '../engine/index';

interface Props {
  points: ReadonlyArray<ChartPoint>;
  threshold: number;
  forceLimit: number;
  /** bump to request a redraw */
  frame: number;
}

const STATE_COLOR: Record<string, string | null> = {
  IDLE: null,
  MONITOR: null,
  CANDIDATE: '#ffcc66',
  CONFIRM: '#ffcc66',
  HOOK_SET: '#4ee0a0',
  RETRIEVE: '#7bb8ff',
  VERIFY: '#9db0ca',
  SAFE_STOP: '#ff7777',
};

/** Lightweight canvas chart (no chart library). Shows the last 10 s at 50 Hz. */
export function TensionChart({ points, threshold, forceLimit, frame }: Props) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
    }
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const N = 500;
    const padL = 34, padB = 10, padT = 8;
    const plotH = h - padB - padT;
    let max = 5;
    let lastBaseline = 0;
    for (const p of points) {
      if (Number.isFinite(p.x)) max = Math.max(max, p.x * 1.1);
      lastBaseline = p.baseline;
    }
    max = Math.max(max, lastBaseline + threshold + 0.5);
    const xOf = (i: number) => padL + ((i + (N - points.length)) / (N - 1)) * (w - padL - 4);
    const yOf = (v: number) => padT + plotH - (v / max) * plotH;

    // grid + y labels
    ctx.font = '10px system-ui, sans-serif';
    ctx.fillStyle = '#7f93ae';
    ctx.strokeStyle = '#1f2f48';
    ctx.lineWidth = 1;
    const step = max > 20 ? 5 : max > 10 ? 2 : 1;
    for (let v = 0; v <= max; v += step) {
      const y = yOf(v);
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(w, y);
      ctx.stroke();
      ctx.fillText(`${v}`, 6, y + 3);
    }

    // state band
    for (let i = 0; i < points.length; i++) {
      const c = STATE_COLOR[points[i].state];
      if (!c) continue;
      ctx.fillStyle = c;
      ctx.globalAlpha = 0.18;
      ctx.fillRect(xOf(i), padT, (w - padL) / N + 0.5, plotH);
      ctx.globalAlpha = 1;
    }

    // force limit
    if (forceLimit < max) {
      ctx.setLineDash([2, 4]);
      ctx.strokeStyle = '#ff7777';
      ctx.beginPath();
      ctx.moveTo(padL, yOf(forceLimit));
      ctx.lineTo(w, yOf(forceLimit));
      ctx.stroke();
      ctx.fillStyle = '#ff9999';
      ctx.fillText('retrieval force limit', w - 112, yOf(forceLimit) - 4);
    }

    // baseline + trigger
    ctx.setLineDash([6, 5]);
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = '#7bb8ff';
    ctx.beginPath();
    points.forEach((p, i) => (i ? ctx.lineTo(xOf(i), yOf(p.baseline)) : ctx.moveTo(xOf(i), yOf(p.baseline))));
    ctx.stroke();
    ctx.strokeStyle = '#ffcc66';
    ctx.beginPath();
    points.forEach((p, i) => (i ? ctx.lineTo(xOf(i), yOf(p.baseline + threshold)) : ctx.moveTo(xOf(i), yOf(p.baseline + threshold))));
    ctx.stroke();
    ctx.setLineDash([]);

    // tension (gaps where sensor invalid)
    ctx.strokeStyle = '#4ee0a0';
    ctx.lineWidth = 2;
    ctx.beginPath();
    let pen = false;
    points.forEach((p, i) => {
      if (!Number.isFinite(p.x)) {
        pen = false;
        return;
      }
      const y = yOf(Math.min(p.x, max));
      if (pen) ctx.lineTo(xOf(i), y);
      else ctx.moveTo(xOf(i), y);
      pen = true;
    });
    ctx.stroke();
    ctx.fillStyle = '#9db0ca';
    ctx.fillText('tension (N)', padL + 6, padT + 10);
  }, [points, threshold, forceLimit, frame]);

  const last = points.length ? points[points.length - 1] : null;
  const desc = last
    ? `Simulated line tension chart, last 10 seconds. Current ${Number.isFinite(last.x) ? last.x.toFixed(2) + ' N' : 'sensor invalid'}, baseline ${last.baseline.toFixed(2)} N, trigger at baseline plus ${threshold.toFixed(1)} N, state ${last.state}.`
    : 'Simulated line tension chart.';
  return (
    <div className="chart-wrap">
      <canvas ref={ref} role="img" aria-label={desc} />
    </div>
  );
}
