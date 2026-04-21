import { createClient } from "@/lib/supabase/server";
import type { BatchRunRow } from "@/lib/supabase/types";
import Link from "next/link";

export const dynamic = "force-dynamic";

function StatusBadge({ status }: { status: BatchRunRow["status"] }) {
  const style =
    status === "done"
      ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200"
      : status === "running" || status === "pending"
        ? "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-200"
        : status === "partial"
          ? "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-200"
          : "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-200";
  return <span className={`rounded px-1.5 py-0.5 text-xs ${style}`}>{status}</span>;
}

export default async function BatchesListPage() {
  const supabase = createClient();
  const { data: batches } = await supabase
    .from("batch_runs")
    .select("*")
    .order("created_at", { ascending: false })
    .limit(50);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">バッチ解析履歴</h1>
        <Link href="/dashboard" className="text-sm text-neutral-500 underline">
          ← 画像一覧
        </Link>
      </div>

      {(batches ?? []).length === 0 ? (
        <p className="rounded border border-dashed border-neutral-300 p-8 text-center text-sm text-neutral-500 dark:border-neutral-700">
          まだバッチはありません。画像一覧で複数選択 → 「バッチ解析」から起動できます。
        </p>
      ) : (
        <ul className="space-y-2">
          {(batches as BatchRunRow[]).map((b) => (
            <li
              key={b.id}
              className="rounded-lg border border-neutral-200 p-3 text-sm dark:border-neutral-800"
            >
              <div className="flex items-center gap-3">
                <StatusBadge status={b.status} />
                <Link href={`/dashboard/batches/${b.id}`} className="font-medium hover:underline">
                  {b.label ?? b.id.slice(0, 8)}
                </Link>
                <span className="text-xs text-neutral-500">
                  {b.succeeded}/{b.total} 成功 · {b.failed} 失敗
                </span>
                <span className="ml-auto text-xs text-neutral-500">
                  {new Date(b.created_at).toLocaleString("ja-JP")}
                </span>
              </div>
              <p className="mt-1 text-xs text-neutral-500">
                pipelines: <code>{b.pipeline_kinds.join(", ")}</code> · images: {b.image_ids.length}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
