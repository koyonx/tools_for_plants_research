"use client";

import { errorMessage } from "@/lib/error-message";
import { createClient } from "@/lib/supabase/client";
import type { AnalysisRow, Co2DiffusionResult } from "@/lib/supabase/types";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8001";
const POLL_INTERVAL_MS = 2500;

type Props = {
  imageId: string;
  imageUrl: string;
  initial: AnalysisRow | null;
  hasSegformerResult: boolean;
  hasCo2MorphResult: boolean;
  canRun: boolean;
  imageWidth: number;
  imageHeight: number;
};

function isCo2DiffusionResult(r: AnalysisRow["result"]): r is Co2DiffusionResult {
  return Boolean(
    r &&
      typeof r === "object" &&
      "ci_pa" in r &&
      "cc_mean_pa" in r &&
      "a_net" in r &&
      "concentration_png_base64" in r,
  );
}

function fmtSci(n: number | null, digits = 3): string {
  if (n === null || !Number.isFinite(n)) return "—";
  return n.toExponential(digits);
}

function fmt(n: number | null, digits = 2): string {
  if (n === null || !Number.isFinite(n)) return "—";
  return n.toFixed(digits);
}

export function Co2DiffusionPanel({
  imageId,
  imageUrl,
  initial,
  hasSegformerResult,
  hasCo2MorphResult,
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
  const [morphReady, setMorphReady] = useState(hasCo2MorphResult);
  const [overlayMode, setOverlayMode] = useState<"concentration" | "drawdown">("concentration");
  const pollTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Live-probe both prereqs.  SegFormer is required; co2_morphometrics
  // is optional — if absent, the solver falls back to mesophyll cells.
  useEffect(() => {
    if (!canRun && segReady && morphReady) return;
    let cancelled = false;
    const probe = async () => {
      const [seg, morph] = await Promise.all([
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
          .eq("kind", "co2_morphometrics")
          .eq("status", "done")
          .order("created_at", { ascending: false })
          .limit(1)
          .maybeSingle<{ id: string }>(),
      ]);
      if (cancelled) return;
      setSegReady(Boolean(seg.data));
      setMorphReady(Boolean(morph.data));
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
  }, [supabase, imageId, canRun, segReady, morphReady]);

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
      const resp = await fetch(`${BACKEND_URL}/images/${imageId}/analyze/co2-diffusion`, {
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
        kind: "co2_diffusion",
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

  const result = analysis && isCo2DiffusionResult(analysis.result) ? analysis.result : null;
  const isBusy = triggering || analysis?.status === "running" || analysis?.status === "pending";
  const overlayB64 =
    result && overlayMode === "concentration"
      ? result.concentration_png_base64
      : (result?.drawdown_png_base64 ?? "");

  return (
    <section className="space-y-4 rounded-lg border border-neutral-200 p-4 dark:border-neutral-800">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-semibold">CO₂ 反応拡散ソルバ</h2>
        {canRun && segReady && (
          <button
            type="button"
            onClick={kickOff}
            disabled={isBusy}
            className="rounded bg-neutral-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-neutral-900"
          >
            {isBusy ? "ソルブ中…" : "CO₂ 拡散場を解く"}
          </button>
        )}
        <span className="text-xs text-neutral-500">
          scipy.sparse 有限体積 / g_m 近似値推定 (Farquhar フィットは PR #13b)
        </span>
      </div>

      {!segReady && (
        <p className="rounded bg-neutral-50 p-3 text-xs text-neutral-600 dark:bg-neutral-900 dark:text-neutral-400">
          このパネルは SegFormer の組織分割結果を前提にします。先に「SegFormer
          組織分割」を実行してください。
        </p>
      )}

      {segReady && !morphReady && (
        <p className="rounded bg-amber-50 p-3 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-200">
          CO₂ 形態パラメータ未完了 — 葉緑体マスクの代わりに葉肉細胞 (柵状 + 海綿状)
          を吸収域として近似します。精度のため先に「CO₂
          拡散モデル形態パラメータ」を実行するのを推奨。
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
        <div className="space-y-4">
          <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-sm sm:grid-cols-[auto_1fr_auto_1fr] lg:grid-cols-[auto_1fr_auto_1fr_auto_1fr]">
            <dt className="text-neutral-500">g_m (近似) [mol/(m²·s·Pa)]</dt>
            <dd className="font-mono">{fmtSci(result.g_m_proxy)}</dd>
            <dt className="text-neutral-500">Cc 平均 [Pa]</dt>
            <dd className="font-mono">{fmt(result.cc_mean_pa, 2)}</dd>
            <dt className="text-neutral-500">Ci [Pa]</dt>
            <dd className="font-mono">{fmt(result.ci_pa, 2)}</dd>
            <dt className="text-neutral-500">降下 平均 [Pa]</dt>
            <dd className="font-mono">{fmt(result.drawdown_mean_pa, 2)}</dd>
            <dt className="text-neutral-500">降下 最大 [Pa]</dt>
            <dd className="font-mono">{fmt(result.drawdown_max_pa, 2)}</dd>
            <dt className="text-neutral-500">A_net [mol/(s·m)]</dt>
            <dd className="font-mono">{fmtSci(result.a_net)}</dd>
            <dt className="text-neutral-500">吸収域</dt>
            <dd className="font-mono">
              {result.sink_class === "chloroplast" ? "葉緑体マスク" : "葉肉細胞 (近似)"}
            </dd>
            <dt className="text-neutral-500">気孔数</dt>
            <dd className="font-mono">{result.stomata_drawdowns.length}</dd>
            <dt className="text-neutral-500">反応速度 r [1/s]</dt>
            <dd className="font-mono">{fmt(result.reaction_rate, 2)}</dd>
          </dl>

          {result.notes.length > 0 && (
            <ul className="list-disc space-y-1 pl-5 text-xs text-neutral-500">
              {result.notes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          )}

          <div className="flex items-center gap-2 text-xs">
            <span className="text-neutral-500">オーバーレイ:</span>
            <button
              type="button"
              onClick={() => setOverlayMode("concentration")}
              className={`rounded border px-2 py-1 ${
                overlayMode === "concentration"
                  ? "border-neutral-900 bg-neutral-900 text-white dark:border-white dark:bg-white dark:text-neutral-900"
                  : "border-neutral-300 dark:border-neutral-700"
              }`}
            >
              CO₂ 濃度場
            </button>
            <button
              type="button"
              onClick={() => setOverlayMode("drawdown")}
              className={`rounded border px-2 py-1 ${
                overlayMode === "drawdown"
                  ? "border-neutral-900 bg-neutral-900 text-white dark:border-white dark:bg-white dark:text-neutral-900"
                  : "border-neutral-300 dark:border-neutral-700"
              }`}
            >
              降下場 (Ci - C)
            </button>
          </div>

          <Co2DiffusionOverlay
            imageUrl={imageUrl}
            width={imageWidth}
            height={imageHeight}
            overlayB64={overlayB64}
            stomata={result.stomata_drawdowns}
          />

          {(() => {
            const params = analysis?.parameters as {
              source_segformer_id?: string;
              source_co2_morphometrics_id?: string | null;
              um_per_px?: number | null;
            } | null;
            const segId = params?.source_segformer_id;
            const morphId = params?.source_co2_morphometrics_id;
            const um = params?.um_per_px;
            return (
              <p className="text-xs text-neutral-500">
                参照した SegFormer:{" "}
                <code className="font-mono">{segId ? segId.slice(0, 8) : "—"}</code>
                {" / co2_morphometrics: "}
                <code className="font-mono">{morphId ? morphId.slice(0, 8) : "—"}</code>
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

function Co2DiffusionOverlay({
  imageUrl,
  width,
  height,
  overlayB64,
  stomata,
}: {
  imageUrl: string;
  width: number;
  height: number;
  overlayB64: string;
  stomata: Co2DiffusionResult["stomata_drawdowns"];
}) {
  const displayWidth = 720;
  const scale = displayWidth / width;
  const displayHeight = height * scale;
  // Encode per-stomatum drawdown as marker radius so users spot
  // stomata seeing low vs high mesophyll Cc at a glance.
  const maxDrop = stomata.reduce((m, s) => Math.max(m, s.drawdown_pa ?? 0), 1e-12);
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
      {overlayB64 && (
        <img
          src={`data:image/png;base64,${overlayB64}`}
          alt="CO2 field overlay"
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
        <title>per-stomatum CO2 drawdown magnitudes</title>
        {stomata.map((s) => {
          const drop = s.drawdown_pa ?? 0;
          const ratio = Math.max(drop / maxDrop, 1e-3);
          const r = Math.max(2, 10 * Math.sqrt(ratio));
          return (
            <circle
              key={`${s.centroid[0]}-${s.centroid[1]}`}
              cx={s.centroid[0]}
              cy={s.centroid[1]}
              r={r}
              fill="rgba(34, 197, 94, 0.7)"
              stroke="rgba(34, 197, 94, 0.95)"
              strokeWidth={Math.max(1, 1.5 / scale)}
            />
          );
        })}
      </svg>
    </div>
  );
}
