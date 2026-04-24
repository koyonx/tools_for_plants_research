"use client";

import { createClient } from "@/lib/supabase/client";
import type { AnalysisRow, WaterPathResult } from "@/lib/supabase/types";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8001";
const POLL_INTERVAL_MS = 2500;

type Props = {
  imageId: string;
  imageUrl: string;
  initial: AnalysisRow | null;
  hasSegformerResult: boolean;
  canRun: boolean;
  imageWidth: number;
  imageHeight: number;
};

function isWaterPathResult(r: AnalysisRow["result"]): r is WaterPathResult {
  return Boolean(
    r && typeof r === "object" && "paths" in r && "heatmap_png_base64" in r && "source_class" in r,
  );
}

export function WaterPathPanel({
  imageId,
  imageUrl,
  initial,
  hasSegformerResult,
  canRun,
  imageWidth,
  imageHeight,
}: Props) {
  const supabase = useMemo(() => createClient(), []);
  const router = useRouter();
  const [analysis, setAnalysis] = useState<AnalysisRow | null>(initial ?? null);
  const [triggering, setTriggering] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Track SegFormer availability live: the parent passes a server-rendered
  // snapshot via `hasSegformerResult`, but the user may run SegFormer in
  // the same page session, so we re-probe Supabase on mount and on focus
  // to flip the panel state without a manual reload.
  const [segReady, setSegReady] = useState(hasSegformerResult);
  const pollTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    // Read-only viewers can't run anything anyway; once a SegFormer
    // result is visible to them, repolling adds no value — skip the
    // setInterval to keep load off the REST gateway when many people
    // share a popular image tab.
    if (!canRun && segReady) return;

    let cancelled = false;
    const probe = async () => {
      const { data } = await supabase
        .from("analyses")
        .select("id")
        .eq("image_id", imageId)
        .eq("kind", "segformer_tissue")
        .eq("status", "done")
        .order("created_at", { ascending: false })
        .limit(1)
        .maybeSingle<{ id: string }>();
      if (!cancelled) setSegReady(Boolean(data));
    };
    void probe();
    const onFocus = () => void probe();
    window.addEventListener("focus", onFocus);
    // Light periodic refresh — SegFormer usually finishes in <1 min, so
    // poll every 10s while the owner has the page open and could trigger
    // a run.  Read-only viewers exit above.
    const interval = canRun ? setInterval(probe, 10_000) : null;
    return () => {
      cancelled = true;
      window.removeEventListener("focus", onFocus);
      if (interval !== null) clearInterval(interval);
    };
  }, [supabase, imageId, canRun, segReady]);

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
          if (row.status === "done" || row.status === "error") {
            terminal = true;
            if (row.status === "done") router.refresh();
          }
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
    if (!canRun || !segReady) return;
    setTriggering(true);
    setError(null);
    clearPoll();
    try {
      const { data: sess } = await supabase.auth.getSession();
      if (!sess.session) throw new Error("セッションが切れました");
      const resp = await fetch(`${BACKEND_URL}/images/${imageId}/analyze/water-path`, {
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
        kind: "water_path",
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

  const result = analysis && isWaterPathResult(analysis.result) ? analysis.result : null;
  const isBusy = triggering || analysis?.status === "running" || analysis?.status === "pending";

  return (
    <section className="space-y-4 rounded-lg border border-neutral-200 p-4 dark:border-neutral-800">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-semibold">水経路（FMM）</h2>
        {canRun && segReady && (
          <button
            type="button"
            onClick={kickOff}
            disabled={isBusy}
            className="rounded bg-neutral-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-neutral-900"
          >
            {isBusy ? "推論中…" : "水経路を計算"}
          </button>
        )}
        <span className="text-xs text-neutral-500">scikit-fmm Fast Marching</span>
      </div>

      {!segReady && (
        <p className="rounded bg-neutral-50 p-3 text-xs text-neutral-600 dark:bg-neutral-900 dark:text-neutral-400">
          このパネルは SegFormer の結果から導管・気孔のマスクを取り出して使います。
          まず上の「SegFormer 組織分割」を実行してください（完了後この案内は自動的に消えます）。
        </p>
      )}

      {segReady && !canRun && (
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
          <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-3">
            <dt className="text-neutral-500">ソース</dt>
            <dd className="font-mono">
              {result.source_class === "xylem_vessel" ? "導管" : "木部 (代用)"}
            </dd>
            <dt className="text-neutral-500">気孔数</dt>
            <dd className="font-mono">{result.sink_count}</dd>
            <dt className="text-neutral-500">平均 travel time</dt>
            <dd className="font-mono">{result.travel_time_mean.toFixed(2)}</dd>
            <dt className="text-neutral-500">中央 travel time</dt>
            <dd className="font-mono">{result.travel_time_p50.toFixed(2)}</dd>
            <dt className="text-neutral-500">最小 / 最大</dt>
            <dd className="font-mono">
              {result.travel_time_min.toFixed(2)} / {result.travel_time_max.toFixed(2)}
            </dd>
          </dl>
          <WaterPathOverlay
            imageUrl={imageUrl}
            width={imageWidth}
            height={imageHeight}
            result={result}
          />
          {(() => {
            const params = analysis?.parameters as { source_segformer_id?: string } | null;
            const sid = params?.source_segformer_id;
            return sid ? (
              <p className="text-xs text-neutral-500">
                参照した SegFormer 結果: <code className="font-mono">{sid.slice(0, 8)}</code>
                {analysis?.created_at &&
                  ` · ${new Date(analysis.created_at).toLocaleString("ja-JP")}`}
              </p>
            ) : null;
          })()}
        </div>
      )}
    </section>
  );
}

function WaterPathOverlay({
  imageUrl,
  width,
  height,
  result,
}: {
  imageUrl: string;
  width: number;
  height: number;
  result: WaterPathResult;
}) {
  const displayWidth = 720;
  const scale = displayWidth / width;
  const displayHeight = height * scale;
  const strokePx = Math.max(1, 1.5 / scale);
  const sourceR = Math.max(2, 4 / scale);
  const sinkR = Math.max(2, 5 / scale);

  return (
    <div
      className="relative overflow-hidden rounded border border-neutral-200 bg-neutral-50 dark:border-neutral-800 dark:bg-neutral-900"
      style={{ width: displayWidth, height: displayHeight }}
    >
      <img
        src={imageUrl}
        alt="leaf section"
        className="absolute inset-0 h-full w-full object-contain"
        draggable={false}
      />
      {result.heatmap_png_base64 && (
        <img
          src={`data:image/png;base64,${result.heatmap_png_base64}`}
          alt="travel-time heatmap"
          className="absolute inset-0 h-full w-full object-contain mix-blend-screen opacity-80"
          draggable={false}
        />
      )}
      <svg
        className="absolute inset-0 h-full w-full"
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="xMidYMid meet"
        aria-hidden
      >
        <title>shortest paths from xylem to stomata</title>
        {result.paths.map((p, i) => {
          // Prefer the gradient-descent polyline; fall back to a
          // straight line for legacy result rows that lack `route`.
          const route = p.route && p.route.length >= 2 ? p.route : null;
          const truncated = Boolean(p.truncated);
          // When truncated, the FINAL segment was an Euclidean snap and
          // may cross high-cost tissue.  Render it dashed and the
          // preceding gradient-traced part solid so the user sees the
          // distinction.
          let solidPoints: string;
          let dashedPoints: string | null = null;
          if (route && truncated && route.length >= 2) {
            const head = route.slice(0, -1);
            const tail = [route[route.length - 2], route[route.length - 1]];
            solidPoints = head.map(([x, y]) => `${x},${y}`).join(" ");
            dashedPoints = tail.map(([x, y]) => `${x},${y}`).join(" ");
          } else if (route) {
            solidPoints = route.map(([x, y]) => `${x},${y}`).join(" ");
          } else {
            solidPoints = `${p.centroid[0]},${p.centroid[1]} ${p.nearest_source[0]},${p.nearest_source[1]}`;
          }
          return (
            <g key={`p${i}-${p.centroid[0].toFixed(1)}`}>
              <polyline
                points={solidPoints}
                fill="none"
                stroke="rgba(56, 189, 248, 0.9)"
                strokeWidth={strokePx}
              />
              {dashedPoints && (
                <polyline
                  points={dashedPoints}
                  fill="none"
                  stroke="rgba(56, 189, 248, 0.7)"
                  strokeWidth={strokePx}
                  strokeDasharray={`${strokePx * 4} ${strokePx * 3}`}
                />
              )}
              <circle
                cx={p.nearest_source[0]}
                cy={p.nearest_source[1]}
                r={sourceR}
                fill="rgba(29, 78, 216, 0.95)"
              />
              <circle
                cx={p.centroid[0]}
                cy={p.centroid[1]}
                r={sinkR}
                fill="rgba(236, 72, 153, 0.9)"
              />
            </g>
          );
        })}
      </svg>
    </div>
  );
}
