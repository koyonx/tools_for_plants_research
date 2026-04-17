"use client";

type Props = {
  x: number[];
  y: number[];
  xLabel?: string;
  yLabel?: string;
};

export function ThicknessChart({ x, y, xLabel = "x (µm)", yLabel = "thickness (µm)" }: Props) {
  if (x.length === 0 || y.length === 0 || x.length !== y.length) {
    return <p className="text-sm text-neutral-500">プロファイルデータなし</p>;
  }

  const width = 720;
  const height = 240;
  const pad = { top: 16, right: 16, bottom: 32, left: 48 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;

  const xMin = x[0];
  const xMax = x[x.length - 1];
  const yMin = Math.min(...y);
  const yMax = Math.max(...y);
  const yPad = (yMax - yMin || 1) * 0.1;
  const yLo = Math.max(0, yMin - yPad);
  const yHi = yMax + yPad;

  const sx = (v: number) => pad.left + ((v - xMin) / (xMax - xMin || 1)) * plotW;
  const sy = (v: number) => pad.top + plotH - ((v - yLo) / (yHi - yLo || 1)) * plotH;

  const path = y
    .map((v, i) => `${i === 0 ? "M" : "L"} ${sx(x[i]).toFixed(1)} ${sy(v).toFixed(1)}`)
    .join(" ");

  const yTicks = 4;
  const yTickVals = Array.from({ length: yTicks + 1 }, (_, i) => yLo + (i * (yHi - yLo)) / yTicks);
  const xTicks = 4;
  const xTickVals = Array.from(
    { length: xTicks + 1 },
    (_, i) => xMin + (i * (xMax - xMin)) / xTicks,
  );

  return (
    <svg
      role="img"
      aria-label="thickness profile chart"
      viewBox={`0 0 ${width} ${height}`}
      className="w-full"
    >
      <title>Thickness profile</title>
      {/* axes */}
      <line
        x1={pad.left}
        y1={pad.top}
        x2={pad.left}
        y2={pad.top + plotH}
        stroke="currentColor"
        strokeOpacity="0.3"
      />
      <line
        x1={pad.left}
        y1={pad.top + plotH}
        x2={pad.left + plotW}
        y2={pad.top + plotH}
        stroke="currentColor"
        strokeOpacity="0.3"
      />
      {/* y-grid + ticks */}
      {yTickVals.map((v) => (
        <g key={`y${v}`}>
          <line
            x1={pad.left}
            y1={sy(v)}
            x2={pad.left + plotW}
            y2={sy(v)}
            stroke="currentColor"
            strokeOpacity="0.08"
          />
          <text
            x={pad.left - 6}
            y={sy(v)}
            textAnchor="end"
            dominantBaseline="middle"
            fontSize="10"
            fill="currentColor"
            opacity="0.7"
          >
            {v.toFixed(0)}
          </text>
        </g>
      ))}
      {xTickVals.map((v) => (
        <g key={`x${v}`}>
          <text
            x={sx(v)}
            y={pad.top + plotH + 14}
            textAnchor="middle"
            fontSize="10"
            fill="currentColor"
            opacity="0.7"
          >
            {v.toFixed(0)}
          </text>
        </g>
      ))}
      <text
        x={pad.left + plotW / 2}
        y={height - 4}
        textAnchor="middle"
        fontSize="11"
        fill="currentColor"
        opacity="0.7"
      >
        {xLabel}
      </text>
      <text
        x={-pad.top - plotH / 2}
        y={14}
        textAnchor="middle"
        fontSize="11"
        fill="currentColor"
        opacity="0.7"
        transform={`rotate(-90 ${14} ${pad.top + plotH / 2})`}
      >
        {yLabel}
      </text>
      {/* series */}
      <path d={path} fill="none" stroke="#2563eb" strokeWidth="1.5" />
    </svg>
  );
}
