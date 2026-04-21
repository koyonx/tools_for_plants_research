import { BatchRunDetail } from "@/components/BatchRunDetail";
import { createClient } from "@/lib/supabase/server";
import type { BatchRunRow } from "@/lib/supabase/types";
import Link from "next/link";
import { notFound } from "next/navigation";

export const dynamic = "force-dynamic";

export default async function BatchDetailPage({ params }: { params: { id: string } }) {
  const supabase = createClient();
  const { data: batch, error } = await supabase
    .from("batch_runs")
    .select("*")
    .eq("id", params.id)
    .maybeSingle<BatchRunRow>();

  if (error) {
    return (
      <p className="rounded bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950 dark:text-red-200">
        読み込みエラー: {error.message}
      </p>
    );
  }
  if (!batch) notFound();

  return (
    <div className="space-y-4">
      <Link href="/dashboard/batches" className="text-sm text-neutral-500 underline">
        ← 履歴
      </Link>
      <h1 className="text-xl font-semibold">{batch.label ?? `batch ${batch.id.slice(0, 8)}`}</h1>
      <BatchRunDetail initial={batch} />
    </div>
  );
}
