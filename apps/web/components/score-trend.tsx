/**
 * A tiny inline SVG sparkline of the score across iterations — the visual proof
 * that the loop is actually improving its work. A dashed line marks the target.
 */
export function ScoreTrend({
  scores,
  target,
  height = 64,
}: {
  scores: number[];
  target: number;
  height?: number;
}) {
  const width = 100;
  const targetY = height - (target / 100) * height;

  if (scores.length === 0) {
    return (
      <div
        className="flex items-center justify-center rounded-lg border border-black/10 text-xs opacity-50 dark:border-white/10"
        style={{ height }}
      >
        No iterations yet
      </div>
    );
  }

  const stepX = scores.length > 1 ? width / (scores.length - 1) : 0;
  const points = scores.map((s, i) => {
    const x = scores.length > 1 ? i * stepX : width / 2;
    const y = height - (s / 100) * height;
    return { x, y, s };
  });
  const path = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ');

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className="w-full"
      style={{ height }}
      role="img"
      aria-label={`Score trend, latest ${scores[scores.length - 1]} of 100`}
    >
      <line
        x1="0"
        x2={width}
        y1={targetY}
        y2={targetY}
        stroke="currentColor"
        strokeWidth="0.5"
        strokeDasharray="2 2"
        className="text-green-500/60"
      />
      <path d={path} fill="none" stroke="currentColor" strokeWidth="1.5" className="text-blue-500" />
      {points.map((p, i) => (
        <circle key={i} cx={p.x} cy={p.y} r="1.6" className="fill-blue-500" />
      ))}
    </svg>
  );
}
