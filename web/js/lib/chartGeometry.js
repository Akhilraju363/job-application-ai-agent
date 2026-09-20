// Pure chart maths (no DOM) -- unit-tested in web/tests.

// Round the axis maximum up to a tidy value so gridlines land on whole numbers.
export function niceMax(v) {
  if (!(v > 0)) return 4;
  if (v <= 4) return 4;
  const pow = 10 ** Math.floor(Math.log10(v));
  for (const step of [1, 2, 2.5, 5, 10]) {
    const candidate = step * pow;
    if (candidate >= v) return Math.ceil(candidate);
  }
  return Math.ceil(v);
}

export function gridTicks(max, count = 4) {
  const step = max / count;
  return Array.from({ length: count + 1 }, (_, i) => Math.round(step * i * 100) / 100);
}

// Donut segments as stroke-dasharray/offset pairs on a circle of radius r.
export function donutArcs(items, r) {
  const total = items.reduce((s, i) => s + i.value, 0);
  const c = 2 * Math.PI * r;
  let acc = 0;
  return items.map((item) => {
    const len = total ? (item.value / total) * c : 0;
    const arc = { ...item, dash: len, gap: c - len, offset: acc ? -acc : 0, circumference: c };
    acc += len;
    return arc;
  });
}

export function percentOf(value, total) {
  return total ? Math.round((100 * value) / total) : 0;
}
