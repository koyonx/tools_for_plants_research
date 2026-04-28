"use client";

import { createClient } from "@/lib/supabase/client";
import type { AnalysisRow, Co2MorphometricsResult } from "@/lib/supabase/types";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { errorMessage } from "@/lib/error-message";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8001";
const POLL_INTERVAL_MS = 2500;

type Props = {
  imageId: string;
  imageUrl: string;
  initial: AnalysisRow | null;
  // Server-rendered snapshot of prereq readiness.  The panel re-probes
  // live so running SegFormer / Cellpose in the same session flips the
  // button on without a reload.
  hasSegformerResult: boolean;
  hasCellposeResult: boolean;
  canRun: boolean;
  imageWidth: number;
  imageHeight: number;
};

function isCo2Result(r: AnalysisRow["result"]): r is Co2MorphometricsResult {
  return Boolean(
    r && typeof r === "object" && "s_mes_s" in r && "cell_wall" in r && "mesophyll_cells" in r,
  );
}

function fmt(n: number | null, digits = 3): string {
  if (n === null || !Number.isFinite(n)) return "—";
  return n.toFixed(digits);
}

export function Co2MorphometricsPanel({
  imageId,
  imageUrl,
  initial,
  hasSegformerResult,
  hasCellposeResult,
  canRun,
  imageWidth,
  imageHeight,
}: Props) {
  const supabase = useMemo(() => createClient(), []);
  const router = useRouter();
  const [analysis, setAnalysis] = useState<AnalysisRow | null>(initial ?? null);
  const [triggering, setTriggering] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [segReady, setSegReady] = useState(hasSegformerResult);
  const [cellposeReady, setCellposeReady] = useState(hasCellposeResult);
  const pollTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Re-probe prereq readiness for owners so that newly-completed
  // SegFormer / Cellpose runs unblock the kickoff button without a
  // full page reload.  Viewers skip this loop once both are already
  // visible — REST traffic serves no purpose when the user can't run.
  useEffect(() => {
    if (!canRun && segReady && cellposeReady) return;

    let cancelled = false;
    const probe = async () => {
      const [seg, cp] = await Promise.all([
        supabase
          .from("analyses")
          .select("id")
          .eq("image_id", imageId)
          .eq("kind", "segformer_tissue")
          .eq("status", "done")
          .order("created_at", { ascending: false })
          .limit(1)
          .maybeSingle<{ id: string }>(),
        supabase
          .from("analyses")
          .select("id")
          .eq("image_id", imageId)
          .eq("kind", "cellpose_cells")
          .eq("status", "done")
          .order("created_at", { ascending: false })
          .limit(1)
          .maybeSingle<{ id: string }>(),
      ]);
      if (cancelled) return;
      setSegReady(Boolean(seg.data));
      setCellposeReady(Boolean(cp.data));
    };
    void probe();
    const onFocus = () => void probe();
    window.addEventListener("focus", onFocus);
    const interval = canRun ? setInterval(probe, 10_000) : null;
    return () => {
      cancelled = true;
      window.removeEventListener("focus", onFocus);
      if (interval !== null) clearInterval(interval);
    };
  }, [supabase, imageId, canRun, segReady, cellposeReady]);

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

  const prereqsReady = segReady && cellposeReady;

  const kickOff = async () => {
    if (!canRun || !prereqsReady) return;
    setTriggering(true);
    setError(null);
    clearPoll();
    try {
      const { data: sess } = await supabase.auth.getSession();
      if (!sess.session) throw new Error("セッションが切れました");
      const resp = await fetch(`${BACKEND_URL}/images/${imageId}/analyze/co2-morphometrics`, {
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
        kind: "co2_morphometrics",
        status: "running",
        parameters: null,
        result: null,
        error: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      });
      pollRefFn.current?.(body.analysis_id);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setTriggering(false);
    }
  };

  const result = analysis && isCo2Result(analysis.result) ? analysis.result : null;
  const isBusy = triggering || analysis?.status === "running" || analysis?.status === "pending";

  return (
    <section className="space-y-4 rounded-lg border border-neutral-200 p-4 dark:border-neutral-800">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-semibold">CO₂ 拡散モデル形態パラメータ</h2>
        {canRun && prereqsReady && (
          <button
            type="button"
            onClick={kickOff}
            disabled={isBusy}
            className="rounded bg-neutral-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-neutral-900"
          >
            {isBusy ? "計算中…" : "S_mes/S・f_ias・T_cw・葉緑体を計算"}
          </button>
        )}
        <span className="text-xs text-neutral-500">
          Evans &amp; von Caemmerer 2-D 近似 / LAB a* 葉緑体検出
        </span>
      </div>

      {!prereqsReady && (
        <p className="rounded bg-neutral-50 p-3 text-xs text-neutral-600 dark:bg-neutral-900 dark:text-neutral-400">
          このパネルは SegFormer と Cellpose の両方の完了結果を前提にします。
          {!segReady && " SegFormer 未完了。"}
          {!cellposeReady && " Cellpose 未完了。"}
          先に両パネルを実行してください（完了すると自動で案内が消えます）。
        </p>
      )}

      {prereqsReady && !canRun && (
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
        <div className="space-y-4">
          <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-sm sm:grid-cols-[auto_1fr_auto_1fr] lg:grid-cols-[auto_1fr_auto_1fr_auto_1fr]">
            <dt className="text-neutral-500">S_mes/S</dt>
            <dd className="font-mono">{fmt(result.s_mes_s)}</dd>
            <dt className="text-neutral-500">S_c/S</dt>
            <dd className="font-mono">{fmt(result.s_c_s)}</dd>
            <dt className="text-neutral-500">f_ias (細胞間隙率)</dt>
            <dd className="font-mono">{fmt(result.f_ias)}</dd>
            <dt className="text-neutral-500">T_cw 中央 (µm)</dt>
            <dd className="font-mono">{fmt(result.cell_wall.t_cw_median_um, 2)}</dd>
            <dt className="text-neutral-500">T_cw 95%tile (µm)</dt>
            <dd className="font-mono">{fmt(result.cell_wall.t_cw_p95_um, 2)}</dd>
            <dt className="text-neutral-500">葉肉層厚 中央 (µm)</dt>
            <dd className="font-mono">{fmt(result.mesophyll.thickness_median_um, 2)}</dd>
            <dt className="text-neutral-500">葉肉内 細胞数</dt>
            <dd className="font-mono">{result.mesophyll_cells.count}</dd>
            <dt className="text-neutral-500">葉緑体数</dt>
            <dd className="font-mono">{result.chloroplasts.count}</dd>
            <dt className="text-neutral-500">葉緑体/細胞面積比</dt>
            <dd className="font-mono">{fmt(result.chloroplasts.coverage_of_mesophyll_cells, 3)}</dd>
          </dl>

          {result.notes.length > 0 && (
            <ul className="list-disc space-y-1 pl-5 text-xs text-neutral-500">
              {result.notes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          )}

          {result.chloroplast_overlay_png_base64 && (
            <Co2Overlay
              imageUrl={imageUrl}
              width={imageWidth}
              height={imageHeight}
              overlayB64={result.chloroplast_overlay_png_base64}
            />
          )}

          {(() => {
            const params = analysis?.parameters as {
              source_segformer_id?: string;
              source_cellpose_id?: string;
              um_per_px?: number | null;
            } | null;
            const segId = params?.source_segformer_id;
            const cpId = params?.source_cellpose_id;
            const um = params?.um_per_px;
            return (
              <p className="text-xs text-neutral-500">
                参照した SegFormer:{" "}
                <code className="font-mono">{segId ? segId.slice(0, 8) : "—"}</code>
                {" / Cellpose: "}
                <code className="font-mono">{cpId ? cpId.slice(0, 8) : "—"}</code>
                {" / scale: "}
                <code className="font-mono">
                  {um !== null && um !== undefined ? `${um.toFixed(3)} µm/px` : "未設定"}
                </code>
                {analysis?.created_at &&
                  ` · ${new Date(analysis.created_at).toLocaleString("ja-JP")}`}
              </p>
            );
          })()}
        </div>
      )}
    </section>
  );
}

function Co2Overlay({
  imageUrl,
  width,
  height,
  overlayB64,
}: {
  imageUrl: string;
  width: number;
  height: number;
  overlayB64: string;
}) {
  const displayWidth = 720;
  const scale = displayWidth / width;
  const displayHeight = height * scale;

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
      <img
        src={`data:image/png;base64,${overlayB64}`}
        alt="chloroplast overlay"
        className="absolute inset-0 h-full w-full object-contain opacity-85"
        draggable={false}
      />
    </div>
  );
}
