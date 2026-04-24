"use client";

import { createClient } from "@/lib/supabase/client";
import type { AnalysisRow, CellposeResult } from "@/lib/supabase/types";
import { useRouter } from "next/navigation";
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

function isCellposeResult(r: AnalysisRow["result"]): r is CellposeResult {
  return Boolean(
    r && typeof r === "object" && "cell_count" in r && "cells" in r && "image_shape" in r,
  );
}

export function CellposePanel({ imageId, imageUrl, initial, umPerPx, canRun }: Props) {
  // `createClient()` returns a fresh SupabaseClient on every call, so
  // memoise here — otherwise every re-render gives us a new reference,
  // poisoning the poll-effect's dependency array.
  const supabase = useMemo(() => createClient(), []);
  const router = useRouter();
  const [analysis, setAnalysis] = useState<AnalysisRow | null>(initial ?? null);
  const [triggering, setTriggering] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Mirror live analysis state into a ref so the poll loop (kept stable
  // across renders) can inspect the latest status without being
  // recreated on every setAnalysis().
  const analysisRef = useRef<AnalysisRow | null>(initial ?? null);
  useEffect(() => {
    analysisRef.current = analysis;
  }, [analysis]);

  // Ref-backed stable identity; safe to pass to useEffect cleanup without
  // tripping Biome's exhaustive-deps rule.
  const clearPollRef = useRef<(() => void) | null>(null);
  if (clearPollRef.current === null) {
    clearPollRef.current = () => {
      if (pollRef.current) {
        clearTimeout(pollRef.current);
        pollRef.current = null;
      }
    };
  }
  const clearPoll = clearPollRef.current;

  // Poll loop with no reactive dependencies.  We pass the JWT grabber +
  // setter in closures captured from the first render; no re-creation
  // on setAnalysis(), so each firing really does fire POLL_INTERVAL_MS
  // apart instead of on every state change.
  const pollRefFn = useRef<((id: string) => void) | null>(null);
  if (pollRefFn.current === null) {
    pollRefFn.current = async (id: string) => {
      // Schedule the next tick unconditionally *unless* we observe a
      // terminal row (done / error).  Transient failures (backend
      // restart mid-run, DNS blip, 500) must not silently stop the
      // loop — the user would otherwise be stuck on a "検出中…" UI
      // with no recovery short of a page reload.
      let terminal = false;
      try {
        const { data: sess } = await supabase.auth.getSession();
        if (!sess.session) return; // signed out — no point rescheduling
        const resp = await fetch(`${BACKEND_URL}/analyses/${id}`, {
          headers: { Authorization: `Bearer ${sess.session.access_token}` },
        });
        if (resp.ok) {
          const row = (await resp.json()) as AnalysisRow;
          setAnalysis(row);
          if (row.status === "done" || row.status === "error") {
            terminal = true;
            // Bump the server page so ValidationBadge picks up the
            // new analysis id (and the literature report re-runs).
            if (row.status === "done") router.refresh();
          }
        }
        // Non-ok responses fall through to re-scheduling below.
      } catch {
        // Network error — same, fall through to re-schedule.
      }
      if (!terminal) {
        pollRef.current = setTimeout(() => pollRefFn.current?.(id), POLL_INTERVAL_MS);
      }
    };
  }

  // Resume polling exactly once per initial `running`/`pending` row.
  // Depending only on primitive id/status values keeps the effect from
  // re-firing on every setAnalysis().
  const initialId = initial?.id ?? null;
  const initialStatus = initial?.status ?? null;
  useEffect(() => {
    if (initialId && (initialStatus === "running" || initialStatus === "pending")) {
      pollRefFn.current?.(initialId);
    }
    return clearPoll;
  }, [initialId, initialStatus, clearPoll]);

  const kickOff = async () => {
    if (!canRun) return;
    setTriggering(true);
    setError(null);
    clearPoll();
    try {
      const { data: sess } = await supabase.auth.getSession();
      if (!sess.session) throw new Error("セッションが切れました");
      const resp = await fetch(`${BACKEND_URL}/images/${imageId}/analyze/cellpose`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${sess.session.access_token}`,
        },
        body: JSON.stringify({ max_side_px: 1024 }),
      });
      if (!resp.ok) {
        const detail = await resp.text().catch(() => "");
        throw new Error(`${resp.status}: ${detail.slice(0, 200)}`);
      }
      const body = (await resp.json()) as { analysis_id: string };
      setAnalysis({
        id: body.analysis_id,
        image_id: imageId,
        kind: "cellpose_cells",
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

  const result = analysis && isCellposeResult(analysis.result) ? analysis.result : null;
  const isBusy = triggering || analysis?.status === "running" || analysis?.status === "pending";
  const areaScale = umPerPx ? umPerPx * umPerPx : null;

  return (
    <section className="space-y-4 rounded-lg border border-neutral-200 p-4 dark:border-neutral-800">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-semibold">Cellpose 細胞検出</h2>
        {canRun && (
          <button
            type="button"
            onClick={kickOff}
            disabled={isBusy}
            className="rounded bg-neutral-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-neutral-900"
          >
            {isBusy ? "検出中… (30–60s)" : "細胞を検出する"}
          </button>
        )}
        <span className="text-xs text-neutral-500">Cellpose 3 cyto3 generalist model</span>
      </div>

      {!canRun && (
        <p className="rounded bg-neutral-50 p-3 text-xs text-neutral-600 dark:bg-neutral-900 dark:text-neutral-400">
          他ユーザーの画像のため、検出の実行はオーナー本人のみ可能です。過去の結果は閲覧できます。
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
          <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-3">
            <dt className="text-neutral-500">検出セル数</dt>
            <dd className="font-mono">{result.cell_count}</dd>
            <dt className="text-neutral-500">平均面積</dt>
            <dd className="font-mono">
              {areaScale
                ? `${(result.mean_area_px * areaScale).toFixed(1)} µm²`
                : `${result.mean_area_px.toFixed(0)} px²`}
            </dd>
            <dt className="text-neutral-500">中央面積</dt>
            <dd className="font-mono">
              {areaScale
                ? `${(result.median_area_px * areaScale).toFixed(1)} µm²`
                : `${result.median_area_px.toFixed(0)} px²`}
            </dd>
          </dl>
          <CellOverlay
            imageUrl={imageUrl}
            width={result.image_shape.width_px}
            height={result.image_shape.height_px}
            cells={result.cells}
          />
        </div>
      )}
    </section>
  );
}

function CellOverlay({
  imageUrl,
  width,
  height,
  cells,
}: {
  imageUrl: string;
  width: number;
  height: number;
  cells: CellposeResult["cells"];
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
        alt="Cellpose overlay"
        className="absolute inset-0 h-full w-full object-contain"
        draggable={false}
      />
      <svg
        className="absolute inset-0 h-full w-full"
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="xMidYMid meet"
        aria-hidden
      >
        <title>detected cells</title>
        {cells.map((c) => (
          <polygon
            key={`${c.centroid[0].toFixed(1)}-${c.centroid[1].toFixed(1)}`}
            points={c.polygon.map(([x, y]) => `${x},${y}`).join(" ")}
            fill="rgba(34, 197, 94, 0.15)"
            stroke="rgba(34, 197, 94, 0.85)"
            strokeWidth={strokeWidthPx}
          />
        ))}
      </svg>
    </div>
  );
}
