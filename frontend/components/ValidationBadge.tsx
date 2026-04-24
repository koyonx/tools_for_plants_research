"use client";

import { createClient } from "@/lib/supabase/client";
import type { ValidationReport } from "@/lib/supabase/types";
import { useCallback, useEffect, useMemo, useState } from "react";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8001";

type Props = {
  /** Either `image` or `session` context — the badge shows a
   *  compact summary for the corresponding validation endpoint. */
  target: { kind: "image"; imageId: string } | { kind: "session"; sessionId: string };
  /** Trigger a re-fetch when this changes — e.g. after a new
   *  analysis completes. */
  refreshKey?: string | number;
};

const BADGE_COLORS = {
  within:
    "bg-emerald-100 text-emerald-800 border-emerald-300 dark:bg-emerald-950 dark:text-emerald-200 dark:border-emerald-800",
  outside:
    "bg-amber-100 text-amber-800 border-amber-300 dark:bg-amber-950 dark:text-amber-200 dark:border-amber-800",
  unknown:
    "bg-neutral-100 text-neutral-600 border-neutral-300 dark:bg-neutral-900 dark:text-neutral-400 dark:border-neutral-700",
  error:
    "bg-red-100 text-red-800 border-red-300 dark:bg-red-950 dark:text-red-200 dark:border-red-800",
};

export function ValidationBadge({ target, refreshKey }: Props) {
  const supabase = useMemo(() => createClient(), []);
  const [report, setReport] = useState<ValidationReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);

  const fetchReport = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const { data: sess } = await supabase.auth.getSession();
      if (!sess.session) throw new Error("セッションが切れました");
      const path =
        target.kind === "image"
          ? `/images/${target.imageId}/validate`
          : `/gas-exchange/sessions/${target.sessionId}/validate`;
      const resp = await fetch(`${BACKEND_URL}${path}`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${sess.session.access_token}`,
        },
      });
      if (!resp.ok) {
        const detail = await resp.text().catch(() => "");
        throw new Error(`${resp.status}: ${detail.slice(0, 240)}`);
      }
      const body = (await resp.json()) as { report: ValidationReport };
      setReport(body.report);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [supabase, target]);

  useEffect(() => {
    // `refreshKey` is intentionally in the deps so parent-driven
    // re-runs (e.g. after a new gm_fit lands) trigger a re-fetch,
    // even though it isn't read in the effect body.
    void refreshKey;
    void fetchReport();
  }, [fetchReport, refreshKey]);

  if (loading && !report) {
    return (
      <span className="inline-flex items-center rounded-full border border-neutral-300 bg-white px-2 py-0.5 text-xs text-neutral-500 dark:border-neutral-700 dark:bg-neutral-900">
        文献照合中…
      </span>
    );
  }

  if (error) {
    return (
      <span
        className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs ${BADGE_COLORS.error}`}
        title={error}
      >
        文献照合エラー
      </span>
    );
  }

  if (!report) {
    return null;
  }

  const total = report.n_within + report.n_outside + report.n_unknown;
  if (total === 0) {
    return (
      <span
        className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs ${BADGE_COLORS.unknown}`}
      >
        文献照合: 対象なし
      </span>
    );
  }

  const primaryColor =
    report.n_outside > 0
      ? BADGE_COLORS.outside
      : report.n_within > 0
        ? BADGE_COLORS.within
        : BADGE_COLORS.unknown;

  return (
    <span className="inline-flex flex-col items-start gap-1 text-xs">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className={`inline-flex items-center gap-2 rounded-full border px-2 py-0.5 ${primaryColor}`}
        title="クリックで文献照合の詳細を表示"
      >
        <span>文献照合</span>
        <span className="font-mono">
          {report.n_within}/{total} 範囲内
          {report.n_outside > 0 ? ` · ${report.n_outside} 逸脱` : ""}
          {report.n_unknown > 0 ? ` · ${report.n_unknown} 不明` : ""}
        </span>
      </button>
      {expanded && report.findings.length > 0 && (
        <div className="mt-1 max-w-xl rounded border border-neutral-200 bg-white p-2 text-xs dark:border-neutral-800 dark:bg-neutral-950">
          <ul className="space-y-1">
            {report.findings.map((f) => (
              <li
                key={`${f.analysis_kind}::${f.parameter_key}`}
                className={`flex flex-wrap items-baseline gap-2 ${
                  f.status === "above" || f.status === "below"
                    ? "text-amber-700 dark:text-amber-300"
                    : f.status === "within"
                      ? "text-emerald-700 dark:text-emerald-300"
                      : "text-neutral-500"
                }`}
              >
                <code className="font-mono">{f.parameter_key}</code>
                <span>
                  {f.measured !== null ? f.measured.toPrecision(3) : "—"} {f.unit}
                </span>
                <span>→ {f.status}</span>
                {f.range_min !== null && f.range_max !== null && (
                  <span className="font-mono text-[11px] text-neutral-500">
                    [{f.range_min.toPrecision(3)}, {f.range_max.toPrecision(3)}] · {f.source}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </span>
  );
}
