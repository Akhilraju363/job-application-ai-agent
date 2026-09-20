// ApplicationOverviewChart (bars) and ApplicationStatusChart (donut) -- hand-rolled SVG,
// driven entirely by API data. Geometry lives in lib/chartGeometry.js (unit-tested).
import { h, svg } from '../dom.js';
import { niceMax, gridTicks, donutArcs, percentOf } from '../lib/chartGeometry.js';

const W = 560, H = 300, PAD = { l: 42, r: 12, t: 18, b: 40 };

export function overviewChart(series) {
  const values = series.map((s) => s.value ?? 0);
  const max = niceMax(Math.max(0, ...values));
  const plotW = W - PAD.l - PAD.r, plotH = H - PAD.t - PAD.b;
  const y = (v) => PAD.t + plotH - (v / max) * plotH;
  const slot = plotW / series.length, barW = Math.min(64, slot * 0.6);

  const grid = gridTicks(max).flatMap((t) => [
    svg('line', { class: 'grid-line', x1: PAD.l, x2: W - PAD.r, y1: y(t), y2: y(t) }),
    svg('text', { class: 'axis-label', x: PAD.l - 8, y: y(t) + 4, 'text-anchor': 'end' }, String(t)),
  ]);

  const bars = series.map((s, i) => {
    const cx = PAD.l + slot * i + slot / 2;
    const unavailable = s.value == null;
    const v = s.value ?? 0;
    return svg('g', { class: `bar-group bar-${s.key}` },
      svg('title', {}, unavailable ? `${s.label}: unavailable` : `${s.label}: ${v}`),
      unavailable
        ? svg('text', { class: 'axis-label', x: cx, y: y(0) - 8, 'text-anchor': 'middle' }, 'n/a')
        : svg('rect', { class: 'bar', x: cx - barW / 2, y: y(v), width: barW, height: Math.max(v ? 2 : 0, y(0) - y(v)), rx: 6 }),
      !unavailable && svg('text', { class: 'bar-value', x: cx, y: y(v) - 6, 'text-anchor': 'middle' }, String(v)),
      svg('text', { class: 'axis-label', x: cx, y: H - 14, 'text-anchor': 'middle' }, s.label));
  });

  return svg('svg', {
    class: 'chart', viewBox: `0 0 ${W} ${H}`, role: 'img', preserveAspectRatio: 'xMidYMid meet',
    'aria-label': 'Applications overview: ' + series.map((s) => `${s.label} ${s.value ?? 'unavailable'}`).join(', '),
  }, ...grid, ...bars);
}

export const STATUS_KEYS = {
  'Not Applied': 'grey', 'Applied': 'blue', 'Interviewing': 'orange', 'Offer': 'green', 'Rejected': 'red',
};

export function statusChart({ statuses, total }, onSelect) {
  const r = 70, sw = 28, size = 200, c = size / 2;
  const arcs = donutArcs(statuses.map((s) => ({ key: s.status, value: s.count })), r);
  const segments = arcs.filter((a) => a.value > 0).map((a) =>
    svg('circle', {
      class: `donut-seg seg-${STATUS_KEYS[a.key]}`, cx: c, cy: c, r, fill: 'none', 'stroke-width': sw,
      'stroke-dasharray': `${a.dash} ${a.gap}`, 'stroke-dashoffset': a.offset,
      transform: `rotate(-90 ${c} ${c})`, tabindex: '0', role: 'link',
      'aria-label': `${a.key}: ${a.value}. View applications`,
      onClick: () => onSelect(a.key),
      onKeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(a.key); } },
    }, svg('title', {}, `${a.key}: ${a.value}`)));

  const donut = svg('svg', { class: 'donut', viewBox: `0 0 ${size} ${size}`, role: 'img', 'aria-label': `${total} applications by status` },
    svg('circle', { class: 'donut-track', cx: c, cy: c, r, fill: 'none', 'stroke-width': sw }),
    ...segments,
    svg('text', { class: 'donut-total', x: c, y: c + 4, 'text-anchor': 'middle' }, String(total)),
    svg('text', { class: 'donut-sub', x: c, y: c + 24, 'text-anchor': 'middle' }, 'Total'));

  const legend = h('ul', { class: 'legend' }, ...statuses.map((s) =>
    h('li', {}, h('button', { class: 'legend-item', onClick: () => onSelect(s.status), title: `View ${s.status} applications` },
      h('span', { class: `dot dot-${STATUS_KEYS[s.status]}` }), h('span', { class: 'legend-name' }, s.status),
      h('span', { class: 'legend-count' }, `${s.count} (${percentOf(s.count, total)}%)`)))));

  return h('div', { class: 'donut-wrap' }, donut, legend);
}
