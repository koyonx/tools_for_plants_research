"use client";

import { createClient } from "@/lib/supabase/client";
import type { AnalysisRow, BasicMeasurementResult } from "@/lib/supabase/types";
import { useState } from "react";
import { ThicknessChart } from "./ThicknessChart";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8001";

type Props = {
  imageId: string;
  initial?: AnalysisRow | null;
  canRun: boolean;
};

function isBasicResult(r: AnalysisRow["result"]): r is BasicMeasurementResult {
  return Boolean(r && typeof r === "object" && "measurement" in r && "scale" in r);
}

export function AnalyzePanel({ imageId, initial, canRun }: Props) {
  const supabase = createClient();
  const [referenceUm, setReferenceUm] = useState("100");
  const [analysis, setAnalysis] = useState<AnalysisRow | null>(initial ?? null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const runAnalysis = async () => {
    setError(null);
    setRunning(true);
    try {
      const num = Number(referenceUm);
      if (!Number.isFinite(num) || num <= 0) {
        throw new Error("reference_um は正の数を指定してください");
      }
      const { data: sess, error: sessErr } = await supabase.auth.getSession();
      if (sessErr || !sess.session) throw new Error("セッションが切れました");
      const token = sess.session.access_token;

      const resp = await fetch(`${BACKEND_URL}/images/${imageId}/analyze`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ reference_um: num }),
      });
      if (!resp.ok) {
        const detail = await resp.text();
        throw new Error(`解析失敗 (${resp.status}): ${detail.slice(0, 400)}`);
      }
      const body = (await resp.json()) as AnalysisRow;
      setAnalysis(body);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  };

  const downloadCsv = async () => {
    if (!analysis) return;
    const { data: sess } = await supabase.auth.getSession();
    if (!sess.session) return;
    const resp = await fetch(`${BACKEND_URL}/analyses/${analysis.id}/csv`, {
      headers: { Authorization: `Bearer ${sess.session.access_token}` },
    });
    if (!resp.ok) return;
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `analysis_${analysis.id}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  const result = analysis && isBasicResult(analysis.result) ? analysis.result : null;

  return (
    <section className="space-y-4 rounded-lg border border-neutral-200 p-4 dark:border-neutral-800">
      <div className="flex flex-wrap items-end gap-3">
        <h2 className="text-lg font-semibold">基本計測</h2>
        {canRun && (
          <>
            <label className="flex items-center gap-2 text-sm">
              スケールバー長 (µm)
              <input
                type="number"
                min="0"
                step="any"
                value={referenceUm}
                onChange={(e) => setReferenceUm(e.target.value)}
                className="w-24 rounded border border-neutral-300 bg-transparent px-2 py-1 dark:border-neutral-700"
              />
            </label>
            <button
              type="button"
              onClick={runAnalysis}
              disabled={running}
              className="rounded bg-neutral-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-neutral-900"
            >
              {running ? "解析中…" : "解析する"}
            </button>
          </>
        )}
        {analysis && (
          <button
            type="button"
            onClick={downloadCsv}
            className="rounded border border-neutral-300 px-3 py-1.5 text-sm hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-900"
          >
            CSV
          </button>
        )}
      </div>

      {!canRun && (
        <p className="rounded bg-neutral-50 p-3 text-xs text-neutral-600 dark:bg-neutral-900 dark:text-neutral-400">
          他のユーザーがオーナーの画像のため、解析の実行はオーナー本人のみ可能です。過去の結果は閲覧できます。
        </p>
      )}

      {error && (
        <p className="rounded bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950 dark:text-red-200">
          {error}
        </p>
      )}

      {analysis && analysis.status === "error" && (
        <p className="rounded bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950 dark:text-red-200">
          パイプラインエラー: {analysis.error ?? "unknown"}
        </p>
      )}

      {result && (
        <div className="space-y-4">
          <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-3">
            <dt className="text-neutral-500">µm/px</dt>
            <dd className="font-mono">{result.scale.um_per_px.toFixed(4)}</dd>
            <dt className="text-neutral-500">葉面積</dt>
            <dd className="font-mono">{result.measurement.leaf_area_um2.toFixed(0)} µm²</dd>
            <dt className="text-neutral-500">平均厚み</dt>
            <dd className="font-mono">{result.measurement.leaf_mean_thickness_um.toFixed(1)} µm</dd>
            <dt className="text-neutral-500">中央厚み</dt>
            <dd className="font-mono">
              {result.measurement.leaf_median_thickness_um.toFixed(1)} µm
            </dd>
            <dt className="text-neutral-500">最小厚み</dt>
            <dd className="font-mono">{result.measurement.leaf_min_thickness_um.toFixed(1)} µm</dd>
            <dt className="text-neutral-500">最大厚み</dt>
            <dd className="font-mono">{result.measurement.leaf_max_thickness_um.toFixed(1)} µm</dd>
          </dl>
          <ThicknessChart
            x={result.measurement.thickness_profile_x_um}
            y={result.measurement.thickness_profile_um}
          />
        </div>
      )}
    </section>
  );
}
