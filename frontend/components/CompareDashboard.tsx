"use client";

import { errorMessage } from "@/lib/error-message";
import { createClient } from "@/lib/supabase/client";
import type {
  CompareMetricDef,
  CompareMetricResult,
  CompareResponse,
  ImageRow,
  PhotosynthesisType,
} from "@/lib/supabase/types";
import { useEffect, useMemo, useState } from "react";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8001";

const PS_TYPES: (PhotosynthesisType | "")[] = ["", "C3", "C4", "C3-C4", "CAM", "unknown"];

type MinImage = Pick<ImageRow, "species" | "photosynthesis_type" | "plant_id" | "treatment">;

type Filter = {
  species: string;
  photosynthesis_type: PhotosynthesisType | "";
  plant_id: string;
  treatment: string;
};

const EMPTY_FILTER: Filter = {
  species: "",
  photosynthesis_type: "",
  plant_id: "",
  treatment: "",
};

function uniq<T>(vals: (T | null | undefined)[]): T[] {
  return Array.from(new Set(vals.filter((v): v is T => Boolean(v))));
}

function fmt(n: number | null, digits = 3): string {
  if (n === null || !Number.isFinite(n)) return "—";
  return n.toFixed(digits);
}

function filterToBody(f: Filter) {
  const out: Record<string, string> = {};
  if (f.species) out.species = f.species;
  if (f.photosynthesis_type) out.photosynthesis_type = f.photosynthesis_type;
  if (f.plant_id) out.plant_id = f.plant_id;
  if (f.treatment) out.treatment = f.treatment;
  return out;
}

export function CompareDashboard({ images }: { images: MinImage[] }) {
  const supabase = useMemo(() => createClient(), []);
  const [catalog, setCatalog] = useState<CompareMetricDef[] | null>(null);
  const [groupA, setGroupA] = useState<Filter>({ ...EMPTY_FILTER, photosynthesis_type: "C3" });
  const [groupB, setGroupB] = useState<Filter>({ ...EMPTY_FILTER, photosynthesis_type: "C4" });
  const [selectedMetrics, setSelectedMetrics] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [response, setResponse] = useState<CompareResponse | null>(null);

  useEffect(() => {
    fetch(`${BACKEND_URL}/compare/metrics`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data?.metrics) {
          setCatalog(data.metrics as CompareMetricDef[]);
          // Pre-select the basic-measurement scalars — they're the
          // cheapest to have N>=2 on both sides.
          setSelectedMetrics(
            new Set(
              (data.metrics as CompareMetricDef[])
                .filter((m) => m.analysis_kind === "basic_measurement")
                .map((m) => m.key),
            ),
          );
        }
      })
      .catch(() => {
        setError("metric catalog の取得に失敗しました");
      });
  }, []);

  const speciesOpts = uniq(images.map((i) => i.species));
  const plantOpts = uniq(images.map((i) => i.plant_id));
  const treatmentOpts = uniq(images.map((i) => i.treatment));

  const toggleMetric = (key: string) => {
    setSelectedMetrics((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  // Require both sides to narrow at least one field — otherwise an
  // empty group silently expands to "every image I can read" and the
  // comparison becomes ambiguous (C3 vs … everyone).
  const canRun =
    selectedMetrics.size > 0 &&
    Object.values(groupA).some(Boolean) &&
    Object.values(groupB).some(Boolean);

  const run = async () => {
    setLoading(true);
    setError(null);
    setResponse(null);
    try {
      const { data: sess } = await supabase.auth.getSession();
      if (!sess.session) throw new Error("セッションが切れました");
      const resp = await fetch(`${BACKEND_URL}/compare`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${sess.session.access_token}`,
        },
        body: JSON.stringify({
          group_a: filterToBody(groupA),
          group_b: filterToBody(groupB),
          metrics: Array.from(selectedMetrics),
        }),
      });
      if (!resp.ok) {
        const detail = await resp.text().catch(() => "");
        throw new Error(`${resp.status}: ${detail.slice(0, 240)}`);
      }
      const body = (await resp.json()) as CompareResponse;
      setResponse(body);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  };

  const exportReport = async (format: "markdown" | "csv") => {
    setError(null);
    try {
      const { data: sess } = await supabase.auth.getSession();
      if (!sess.session) throw new Error("セッションが切れました");
      const resp = await fetch(`${BACKEND_URL}/compare/export`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${sess.session.access_token}`,
        },
        body: JSON.stringify({
          group_a: filterToBody(groupA),
          group_b: filterToBody(groupB),
          metrics: Array.from(selectedMetrics),
          format,
        }),
      });
      if (!resp.ok) {
        const detail = await resp.text().catch(() => "");
        throw new Error(`${resp.status}: ${detail.slice(0, 240)}`);
      }
      const text = await resp.text();
      const extension = format === "markdown" ? "md" : "csv";
      const mime = format === "markdown" ? "text/markdown" : "text/csv";
      const blob = new Blob([text], { type: `${mime};charset=utf-8` });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `compare-report.${extension}`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  return (
    <div className="space-y-4 text-sm">
      <p className="text-xs text-neutral-500">
        2 グループのフィルタを選んで、各指標で Welch's t / Mann-Whitney U / Cohen's d / Hedges' g
        (95% bootstrap CI) を計算します。対象は解析済みの 画像のみ（各指標に紐付く <code>done</code>{" "}
        analyses 行を参照）。
      </p>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <GroupEditor
          title="Group A"
          filter={groupA}
          onChange={setGroupA}
          speciesOpts={speciesOpts}
          plantOpts={plantOpts}
          treatmentOpts={treatmentOpts}
          accent="bg-sky-50 dark:bg-sky-950/60"
        />
        <GroupEditor
          title="Group B"
          filter={groupB}
          onChange={setGroupB}
          speciesOpts={speciesOpts}
          plantOpts={plantOpts}
          treatmentOpts={treatmentOpts}
          accent="bg-amber-50 dark:bg-amber-950/60"
        />
      </div>

      <section className="rounded-lg border border-neutral-200 p-3 dark:border-neutral-800">
        <h2 className="font-medium">指標</h2>
        <div className="mt-2 grid grid-cols-1 gap-1 sm:grid-cols-2 lg:grid-cols-3">
          {catalog === null ? (
            <span className="text-xs text-neutral-500">読み込み中…</span>
          ) : (
            catalog.map((m) => (
              <label key={m.key} className="flex items-center gap-2 text-xs">
                <input
                  type="checkbox"
                  checked={selectedMetrics.has(m.key)}
                  onChange={() => toggleMetric(m.key)}
                />
                <span>{m.label}</span>
                <span className="text-neutral-500">({m.unit})</span>
                <span className="ml-auto rounded bg-neutral-100 px-1 font-mono text-[10px] text-neutral-600 dark:bg-neutral-800 dark:text-neutral-400">
                  {m.analysis_kind}
                </span>
              </label>
            ))
          )}
        </div>
      </section>

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={run}
          disabled={!canRun || loading}
          className="rounded bg-neutral-900 px-3 py-1.5 font-medium text-white disabled:opacity-50 dark:bg-white dark:text-neutral-900"
        >
          {loading ? "計算中…" : "比較を実行"}
        </button>
        <button
          type="button"
          onClick={() => void exportReport("markdown")}
          disabled={!canRun || loading}
          className="rounded border border-neutral-300 px-3 py-1.5 text-sm disabled:opacity-50 hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-900"
        >
          Markdown エクスポート
        </button>
        <button
          type="button"
          onClick={() => void exportReport("csv")}
          disabled={!canRun || loading}
          className="rounded border border-neutral-300 px-3 py-1.5 text-sm disabled:opacity-50 hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-900"
        >
          CSV エクスポート
        </button>
        <span className="text-xs text-neutral-500">{selectedMetrics.size} 指標選択中</span>
      </div>

      {error && (
        <p className="rounded bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950 dark:text-red-200">
          {error}
        </p>
      )}

      {response && <CompareResults response={response} />}
    </div>
  );
}

function GroupEditor({
  title,
  filter,
  onChange,
  speciesOpts,
  plantOpts,
  treatmentOpts,
  accent,
}: {
  title: string;
  filter: Filter;
  onChange: (f: Filter) => void;
  speciesOpts: string[];
  plantOpts: string[];
  treatmentOpts: string[];
  accent: string;
}) {
  const set = <K extends keyof Filter>(k: K, v: Filter[K]) => onChange({ ...filter, [k]: v });
  return (
    <section
      className={`space-y-2 rounded-lg border border-neutral-200 p-3 dark:border-neutral-800 ${accent}`}
    >
      <h2 className="font-medium">{title}</h2>
      <div className="grid grid-cols-2 gap-2">
        <select
          value={filter.species}
          onChange={(e) => set("species", e.target.value)}
          className="rounded border border-neutral-300 bg-transparent px-2 py-1 dark:border-neutral-700"
        >
          <option value="">species (any)</option>
          {speciesOpts.map((v) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </select>
        <select
          value={filter.photosynthesis_type}
          onChange={(e) => set("photosynthesis_type", e.target.value as PhotosynthesisType | "")}
          className="rounded border border-neutral-300 bg-transparent px-2 py-1 dark:border-neutral-700"
        >
          {PS_TYPES.map((v) => (
            <option key={v} value={v}>
              {v || "photosynthesis (any)"}
            </option>
          ))}
        </select>
        <select
          value={filter.plant_id}
          onChange={(e) => set("plant_id", e.target.value)}
          className="rounded border border-neutral-300 bg-transparent px-2 py-1 dark:border-neutral-700"
        >
          <option value="">plant_id (any)</option>
          {plantOpts.map((v) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </select>
        <select
          value={filter.treatment}
          onChange={(e) => set("treatment", e.target.value)}
          className="rounded border border-neutral-300 bg-transparent px-2 py-1 dark:border-neutral-700"
        >
          <option value="">treatment (any)</option>
          {treatmentOpts.map((v) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </select>
      </div>
    </section>
  );
}

function CompareResults({ response }: { response: CompareResponse }) {
  return (
    <div className="space-y-4">
      <p className="text-xs text-neutral-500">
        Group A: {response.group_a.image_count} 画像 | Group B: {response.group_b.image_count} 画像
      </p>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[800px] border-collapse text-xs">
          <thead className="text-left text-neutral-500">
            <tr>
              <th className="border-b border-neutral-200 py-1 dark:border-neutral-800">指標</th>
              <th className="border-b border-neutral-200 py-1 text-right dark:border-neutral-800">
                A (n, 平均±SD)
              </th>
              <th className="border-b border-neutral-200 py-1 text-right dark:border-neutral-800">
                B (n, 平均±SD)
              </th>
              <th className="border-b border-neutral-200 py-1 text-right dark:border-neutral-800">
                Welch p
              </th>
              <th className="border-b border-neutral-200 py-1 text-right dark:border-neutral-800">
                MW p
              </th>
              <th className="border-b border-neutral-200 py-1 text-right dark:border-neutral-800">
                Hedges g [95% CI]
              </th>
            </tr>
          </thead>
          <tbody>
            {response.metrics.map((m) => (
              <tr
                key={m.metric.key}
                className="border-b border-neutral-100 align-top dark:border-neutral-900"
              >
                <td className="py-2 pr-2">
                  <div className="font-medium">{m.metric.label}</div>
                  <div className="text-neutral-500">{m.metric.unit}</div>
                </td>
                <td className="py-2 pr-2 text-right font-mono">
                  {m.group_a.n === 0
                    ? "—"
                    : `${m.group_a.n}, ${fmt(m.group_a.mean)} ± ${fmt(m.group_a.sd)}`}
                </td>
                <td className="py-2 pr-2 text-right font-mono">
                  {m.group_b.n === 0
                    ? "—"
                    : `${m.group_b.n}, ${fmt(m.group_b.mean)} ± ${fmt(m.group_b.sd)}`}
                </td>
                <td className="py-2 pr-2 text-right font-mono">{fmt(m.tests.welch_p_value, 4)}</td>
                <td className="py-2 pr-2 text-right font-mono">
                  {fmt(m.tests.mann_whitney_p_value, 4)}
                </td>
                <td className="py-2 pr-2 text-right font-mono">
                  {m.effect_size.hedges_g === null
                    ? "—"
                    : `${fmt(m.effect_size.hedges_g)} [${fmt(m.effect_size.hedges_g_ci_low)}, ${fmt(m.effect_size.hedges_g_ci_high)}]`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {response.metrics.map((m) => (
          <BoxplotCard key={m.metric.key} metric={m} />
        ))}
      </div>
    </div>
  );
}

function BoxplotCard({ metric }: { metric: CompareMetricResult }) {
  const { group_a: a, group_b: b } = metric;
  const hasAnyValue = a.values.length > 0 || b.values.length > 0;
  if (!hasAnyValue) {
    return (
      <section className="rounded-lg border border-neutral-200 p-3 text-xs text-neutral-500 dark:border-neutral-800">
        <h3 className="text-sm font-medium text-neutral-700 dark:text-neutral-300">
          {metric.metric.label}
        </h3>
        <p className="mt-2">両グループとも値がありません。</p>
      </section>
    );
  }
  const allValues = [...a.values, ...b.values];
  const vmin = Math.min(...allValues);
  const vmax = Math.max(...allValues);
  const pad = (vmax - vmin || 1) * 0.1;
  const yLo = vmin - pad;
  const yHi = vmax + pad;

  const width = 420;
  const height = 240;
  const margin = { top: 12, right: 12, bottom: 28, left: 48 };
  const plotW = width - margin.left - margin.right;
  const plotH = height - margin.top - margin.bottom;

  const sy = (v: number) => margin.top + plotH - ((v - yLo) / (yHi - yLo || 1)) * plotH;

  return (
    <section className="rounded-lg border border-neutral-200 p-3 dark:border-neutral-800">
      <h3 className="text-sm font-medium">{metric.metric.label}</h3>
      <p className="text-xs text-neutral-500">
        Hedges g = {fmt(metric.effect_size.hedges_g)}
        {metric.effect_size.hedges_g_ci_low !== null &&
          metric.effect_size.hedges_g_ci_high !== null && (
            <>
              {" "}
              [{fmt(metric.effect_size.hedges_g_ci_low)}, {fmt(metric.effect_size.hedges_g_ci_high)}
              ]
            </>
          )}
        {" · "}Welch p = {fmt(metric.tests.welch_p_value, 4)}
      </p>
      <svg
        role="img"
        aria-label={`${metric.metric.label} boxplot`}
        viewBox={`0 0 ${width} ${height}`}
        className="mt-2 w-full"
      >
        <title>{metric.metric.label}</title>
        {/* y-axis */}
        <line
          x1={margin.left}
          y1={margin.top}
          x2={margin.left}
          y2={margin.top + plotH}
          stroke="currentColor"
          strokeOpacity={0.3}
        />
        {[0, 1, 2, 3, 4].map((i) => {
          const v = yLo + ((yHi - yLo) * i) / 4;
          return (
            <g key={`ytick-${v.toFixed(3)}`}>
              <line
                x1={margin.left - 3}
                y1={sy(v)}
                x2={margin.left + plotW}
                y2={sy(v)}
                stroke="currentColor"
                strokeOpacity={0.1}
              />
              <text
                x={margin.left - 6}
                y={sy(v)}
                textAnchor="end"
                dominantBaseline="middle"
                fontSize={10}
                fill="currentColor"
                opacity={0.7}
              >
                {v.toFixed(1)}
              </text>
            </g>
          );
        })}
        {renderBox(
          a,
          margin.left + plotW * 0.2,
          sy,
          "rgba(56, 189, 248, 0.8)",
          "rgba(56, 189, 248, 1)",
        )}
        {renderBox(
          b,
          margin.left + plotW * 0.6,
          sy,
          "rgba(251, 191, 36, 0.8)",
          "rgba(251, 191, 36, 1)",
        )}
        <text
          x={margin.left + plotW * 0.2}
          y={height - 8}
          textAnchor="middle"
          fontSize={11}
          fill="currentColor"
          opacity={0.7}
        >
          A (n={a.n})
        </text>
        <text
          x={margin.left + plotW * 0.6}
          y={height - 8}
          textAnchor="middle"
          fontSize={11}
          fill="currentColor"
          opacity={0.7}
        >
          B (n={b.n})
        </text>
      </svg>
    </section>
  );
}

function renderBox(
  stats: CompareMetricResult["group_a"],
  cx: number,
  sy: (v: number) => number,
  fill: string,
  stroke: string,
) {
  // n=0 or any summary field null (backend emits null instead of NaN
  // for empty groups) → render nothing.
  if (
    stats.n === 0 ||
    stats.min === null ||
    stats.max === null ||
    stats.q25 === null ||
    stats.q75 === null ||
    stats.median === null
  ) {
    return null;
  }
  const w = 60;
  const left = cx - w / 2;
  const right = cx + w / 2;
  return (
    <g>
      {/* whiskers */}
      <line x1={cx} y1={sy(stats.min)} x2={cx} y2={sy(stats.q25)} stroke={stroke} />
      <line x1={cx} y1={sy(stats.q75)} x2={cx} y2={sy(stats.max)} stroke={stroke} />
      <line x1={left + 10} y1={sy(stats.min)} x2={right - 10} y2={sy(stats.min)} stroke={stroke} />
      <line x1={left + 10} y1={sy(stats.max)} x2={right - 10} y2={sy(stats.max)} stroke={stroke} />
      {/* box */}
      <rect
        x={left}
        y={sy(stats.q75)}
        width={w}
        height={Math.max(1, sy(stats.q25) - sy(stats.q75))}
        fill={fill}
        stroke={stroke}
      />
      {/* median */}
      <line
        x1={left}
        y1={sy(stats.median)}
        x2={right}
        y2={sy(stats.median)}
        stroke={stroke}
        strokeWidth={2}
      />
      {/* jittered points */}
      {stats.values.map((v, i) => {
        // deterministic jitter per index so repaints don't wiggle
        const j = ((i * 2654435761) % 1000) / 1000 - 0.5;
        return (
          <circle
            key={`pt-${i}-${v.toFixed(3)}`}
            cx={cx + j * (w * 0.6)}
            cy={sy(v)}
            r={2}
            fill={stroke}
            fillOpacity={0.5}
          />
        );
      })}
    </g>
  );
}
