"use client";

import { createClient } from "@/lib/supabase/client";
import type { BatchRunRow } from "@/lib/supabase/types";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

const POLL_INTERVAL_MS = 2500;

type Props = {
  initial: BatchRunRow;
};

/**
 * Poll the batch_runs row until it reaches a terminal state.  Shares the
 * terminal-gated poll pattern with the per-analysis panels so transient
 * network errors don't abandon the live counter.
 */
export function BatchRunDetail({ initial }: Props) {
  const supabase = useMemo(() => createClient(), []);
  const [batch, setBatch] = useState<BatchRunRow>(initial);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      const { data } = await supabase
        .from("batch_runs")
        .select("*")
        .eq("id", initial.id)
        .maybeSingle<BatchRunRow>();
      if (!cancelled && data) setBatch(data);
      const terminal =
        data && (data.status === "done" || data.status === "error" || data.status === "partial");
      if (!terminal && !cancelled) {
        timeoutRef.current = setTimeout(tick, POLL_INTERVAL_MS);
      }
    };
    if (initial.status === "running" || initial.status === "pending") {
      timeoutRef.current = setTimeout(tick, POLL_INTERVAL_MS);
    }
    return () => {
      cancelled = true;
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    };
  }, [supabase, initial.id, initial.status]);

  const pct = batch.total > 0 ? ((batch.succeeded + batch.failed) / batch.total) * 100 : 0;

  return (
    <div className="space-y-3 text-sm">
      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1">
        <dt className="text-neutral-500">status</dt>
        <dd>
          <span
            className={`rounded px-1.5 py-0.5 text-xs ${
              batch.status === "done"
                ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200"
                : batch.status === "running" || batch.status === "pending"
                  ? "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-200"
                  : batch.status === "partial"
                    ? "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-200"
                    : "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-200"
            }`}
          >
            {batch.status}
          </span>
        </dd>
        <dt className="text-neutral-500">progress</dt>
        <dd className="font-mono">
          {batch.succeeded + batch.failed} / {batch.total} ({pct.toFixed(0)}%)
        </dd>
        <dt className="text-neutral-500">succeeded</dt>
        <dd className="font-mono">{batch.succeeded}</dd>
        <dt className="text-neutral-500">failed</dt>
        <dd className="font-mono">{batch.failed}</dd>
        <dt className="text-neutral-500">pipelines</dt>
        <dd className="font-mono text-xs">{batch.pipeline_kinds.join(", ")}</dd>
        <dt className="text-neutral-500">images</dt>
        <dd className="font-mono text-xs">{batch.image_ids.length}</dd>
        <dt className="text-neutral-500">created</dt>
        <dd className="text-xs">{new Date(batch.created_at).toLocaleString("ja-JP")}</dd>
        <dt className="text-neutral-500">updated</dt>
        <dd className="text-xs">{new Date(batch.updated_at).toLocaleString("ja-JP")}</dd>
      </dl>

      {batch.error && (
        <p className="rounded bg-red-50 p-2 text-xs text-red-800 dark:bg-red-950 dark:text-red-200">
          最後のエラー: {batch.error}
        </p>
      )}

      <div>
        <h2 className="text-sm font-medium">対象画像</h2>
        <ul className="mt-1 space-y-1">
          {batch.image_ids.map((iid) => (
            <li key={iid} className="text-xs">
              <Link href={`/dashboard/images/${iid}`} className="font-mono hover:underline">
                {iid.slice(0, 8)}…
              </Link>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
