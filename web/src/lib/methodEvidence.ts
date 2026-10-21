/** Engine spans index Unicode code points; String.slice indexes UTF-16 units. */
export function evidenceSegments(text: string, spans: { start: number; end: number; quote: string }[]) {
  const points = Array.from(text);
  const valid = spans.filter(s => Number.isInteger(s.start) && Number.isInteger(s.end)
    && s.start >= 0 && s.end > s.start && s.end <= points.length
    && points.slice(s.start, s.end).join('') === s.quote).sort((a, b) => a.start - b.start);
  const ranges: { start: number; end: number }[] = [];
  for (const span of valid) {
    const tail = ranges[ranges.length - 1];
    if (tail && span.start <= tail.end) tail.end = Math.max(tail.end, span.end);
    else ranges.push({ start: span.start, end: span.end });
  }
  const parts: { text: string; highlighted: boolean }[] = [];
  let cursor = 0;
  for (const range of ranges) {
    if (cursor < range.start) parts.push({ text: points.slice(cursor, range.start).join(''), highlighted: false });
    parts.push({ text: points.slice(range.start, range.end).join(''), highlighted: true });
    cursor = range.end;
  }
  if (cursor < points.length) parts.push({ text: points.slice(cursor).join(''), highlighted: false });
  return parts;
}
