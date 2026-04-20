"use client";

import { createClient } from "@/lib/supabase/client";
import type { AnalysisRow, SegFormerResult } from "@/lib/supabase/types";
import { TISSUE_CLASS_BY_KEY } from "@/lib/tissue-classes";
import { useEffect, useMemo, useRef, useState } from "react";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8001";
const POLL_INTERVAL_MS = 2500;

type Props = {
  imageId: string;
  imageUrl: string;
  initial: AnalysisRow | null;
  umPerPx: number | null;
  canRun: boolean;
};

function isSegFormerResult(r: AnalysisRow["result"]): r is SegFormerResult {
  return Boolean(
    r && typeof r === "object" && "coverage" in r && "polygons" in r && "image_shape" in r,
  );
}

export function SegFormerPanel({ imageId, imageUrl, initial, umPerPx, canRun }: Props) {
  const supabase = useMemo(() => createClient(), []);
  const [analysis, setAnalysis] = useState<AnalysisRow | null>(initial ?? null);
  const [triggering, setTriggering] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [available, setAvailable] = useState<boolean | null>(null);
  const pollTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Probe whether a checkpoint is present on the backend.
  useEffect(() => {
    let cancelled = false;
    fetch(`${BACKEND_URL}/analyze/segformer/status`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!cancelled && data) setAvailable(Boolean(data.available));
      })
      .catch(() => {
        if (!cancelled) setAvailable(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const clearPollRef = useRef<(() => void) | null>(null);
  if (clearPollRef.current === null) {
    clearPollRef.current = () => {
      if (pollTimeoutRef.current) {
        clearTimeout(pollTimeoutRef.current);
        pollTimeoutRef.current = null;
      }
    };
  }
  const clearPoll = clearPollRef.current;

  const pollRefFn = useRef<((id: string) => void) | null>(null);
  if (pollRefFn.current === null) {
    pollRefFn.current = async (id: string) => {
      let terminal = false;
      try {
        const { data: sess } = await supabase.auth.getSession();
        if (!sess.session) return;
        const resp = await fetch(`${BACKEND_URL}/analyses/${id}`, {
          headers: { Authorization: `Bearer ${sess.session.access_token}` },
        });
        if (resp.ok) {
          const row = (await resp.json()) as AnalysisRow;
          setAnalysis(row);
          if (row.status === "done" || row.status === "error") terminal = true;
        }
      } catch {
        // fall through to reschedule
      }
      if (!terminal) {
        pollTimeoutRef.current = setTimeout(() => pollRefFn.current?.(id), POLL_INTERVAL_MS);
      }
    };
  }

  const initialId = initial?.id ?? null;
  const initialStatus = initial?.status ?? null;
  useEffect(() => {
    if (initialId && (initialStatus === "running" || initialStatus === "pending")) {
      pollRefFn.current?.(initialId);
    }
    return clearPoll;
  }, [initialId, initialStatus, clearPoll]);

  const kickOff = async () => {
    if (!canRun || available !== true) return;
    setTriggering(true);
    setError(null);
    clearPoll();
    try {
      const { data: sess } = await supabase.auth.getSession();
      if (!sess.session) throw new Error("セッションが切れました");
      const resp = await fetch(`${BACKEND_URL}/images/${imageId}/analyze/segformer`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${sess.session.access_token}`,
        },
        body: JSON.stringify({ max_side_px: 1024 }),
      });
      if (!resp.ok) {
        const detail = await resp.text().catch(() => "");
        throw new Error(`${resp.status}: ${detail.slice(0, 240)}`);
      }
      const body = (await resp.json()) as { analysis_id: string };
      setAnalysis({
        id: body.analysis_id,
        image_id: imageId,
        kind: "segformer_tissue",
        status: "running",
        parameters: null,
        result: null,
        error: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      });
      pollRefFn.current?.(body.analysis_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setTriggering(false);
    }
  };

  const result = analysis && isSegFormerResult(analysis.result) ? analysis.result : null;
  const isBusy = triggering || analysis?.status === "running" || analysis?.status === "pending";
  const areaScale = umPerPx ? umPerPx * umPerPx : null;

  return (
    <section className="space-y-4 rounded-lg border border-neutral-200 p-4 dark:border-neutral-800">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-semibold">SegFormer 組織分割</h2>
        {canRun && available === true && (
          <button
            type="button"
            onClick={kickOff}
            disabled={isBusy}
            className="rounded bg-neutral-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-neutral-900"
          >
            {isBusy ? "推論中…" : "組織を分割する"}
          </button>
        )}
        <span className="text-xs text-neutral-500">
          ユーザー学習済み SegFormer（
          {available === null ? "状態確認中…" : available ? "checkpoint 検出" : "checkpoint 未配置"}
          ）
        </span>
      </div>

      {available === false && (
        <p className="rounded bg-neutral-50 p-3 text-xs text-neutral-600 dark:bg-neutral-900 dark:text-neutral-400">
          <code>models/segformer/</code> に checkpoint が見つかりません。
          <code>notebooks/segformer_train.ipynb</code> で学習 →
          ドロップインするとこのパネルが有効化されます。
        </p>
      )}

      {!canRun && available === true && (
        <p className="rounded bg-neutral-50 p-3 text-xs text-neutral-600 dark:bg-neutral-900 dark:text-neutral-400">
          他ユーザーの画像のため、推論の実行はオーナー本人のみ可能です。過去の結果は閲覧できます。
        </p>
      )}

      {error && (
        <p className="rounded bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950 dark:text-red-200">
          {error}
        </p>
      )}

      {analysis?.status === "error" && (
        <p className="rounded bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950 dark:text-red-200">
          パイプラインエラー: {analysis.error ?? "unknown"}
        </p>
      )}

      {result && (
        <div className="space-y-3">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-neutral-500">
                <th className="py-1">クラス</th>
                <th className="py-1">面積</th>
                <th className="py-1">割合</th>
              </tr>
            </thead>
            <tbody>
              {result.coverage.map((c) => {
                const cls = TISSUE_CLASS_BY_KEY[c.class_key];
                const label = cls?.label ?? c.class_key;
                return (
                  <tr
                    key={c.class_key}
                    className="border-t border-neutral-200 dark:border-neutral-800"
                  >
                    <td className="py-1">
                      <span
                        aria-hidden
                        className="mr-2 inline-block h-3 w-3 rounded-sm align-middle"
                        style={{ backgroundColor: cls?.color ?? "#888" }}
                      />
                      {label}
                    </td>
                    <td className="py-1 font-mono">
                      {areaScale
                        ? `${(c.area_px * areaScale).toFixed(0)} µm²`
                        : `${c.area_px.toLocaleString()} px²`}
                    </td>
                    <td className="py-1 font-mono">{(c.coverage_ratio * 100).toFixed(1)} %</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <SegFormerOverlay
            imageUrl={imageUrl}
            width={result.image_shape.width_px}
            height={result.image_shape.height_px}
            polygons={result.polygons}
          />
        </div>
      )}
    </section>
  );
}

function SegFormerOverlay({
  imageUrl,
  width,
  height,
  polygons,
}: {
  imageUrl: string;
  width: number;
  height: number;
  polygons: SegFormerResult["polygons"];
}) {
  const displayWidth = 720;
  const scale = displayWidth / width;
  const displayHeight = height * scale;
  const strokeWidthPx = Math.max(1, 1.5 / scale);

  return (
    <div
      className="relative overflow-hidden rounded border border-neutral-200 bg-neutral-50 dark:border-neutral-800 dark:bg-neutral-900"
      style={{ width: displayWidth, height: displayHeight }}
    >
      <img
        src={imageUrl}
        alt="SegFormer overlay"
        className="absolute inset-0 h-full w-full object-contain"
        draggable={false}
      />
      <svg
        className="absolute inset-0 h-full w-full"
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="xMidYMid meet"
        aria-hidden
      >
        <title>tissue segmentation</title>
        {polygons.map((p, i) => {
          const cls = TISSUE_CLASS_BY_KEY[p.class_key];
          const color = cls?.color ?? "#888";
          return (
            <polygon
              key={`${p.class_key}-${i}-${p.polygon[0]?.[0].toFixed(1)}`}
              points={p.polygon.map(([x, y]) => `${x},${y}`).join(" ")}
              fill={hexToRgba(color, 0.22)}
              stroke={color}
              strokeWidth={strokeWidthPx}
            />
          );
        })}
      </svg>
    </div>
  );
}

function hexToRgba(hex: string, alpha: number): string {
  const h = hex.replace("#", "");
  const r = Number.parseInt(h.slice(0, 2), 16);
  const g = Number.parseInt(h.slice(2, 4), 16);
  const b = Number.parseInt(h.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}
