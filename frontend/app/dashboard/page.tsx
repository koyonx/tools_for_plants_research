import { ImageBatchPicker } from "@/components/ImageBatchPicker";
import { TrainingExportButton } from "@/components/TrainingExportButton";
import { createClient } from "@/lib/supabase/server";
import type { ImageRow } from "@/lib/supabase/types";
import Link from "next/link";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  const supabase = createClient();
  const [{ data: images, error }, { data: userData }] = await Promise.all([
    supabase.from("images").select("*").order("created_at", { ascending: false }).limit(300),
    supabase.auth.getUser(),
  ]);
  const currentUserId = userData.user?.id ?? null;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-2xl font-bold tracking-tight">画像一覧</h1>
        <div className="flex items-center gap-3">
          <TrainingExportButton />
          <Link
            href="/dashboard/upload"
            className="rounded bg-neutral-900 px-3 py-2 text-sm font-medium text-white hover:bg-neutral-800 dark:bg-white dark:text-neutral-900 dark:hover:bg-neutral-200"
          >
            + アップロード
          </Link>
        </div>
      </div>

      {error && (
        <p className="rounded bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950 dark:text-red-200">
          読み込みエラー: {error.message}
        </p>
      )}

      {(!images || images.length === 0) && (
        <p className="rounded border border-dashed border-neutral-300 p-8 text-center text-sm text-neutral-500 dark:border-neutral-700">
          まだ画像がありません。右上の「アップロード」から追加してください。
        </p>
      )}

      {images && images.length > 0 && (
        <ImageBatchPicker initial={images as ImageRow[]} currentUserId={currentUserId} />
      )}
    </div>
  );
}
