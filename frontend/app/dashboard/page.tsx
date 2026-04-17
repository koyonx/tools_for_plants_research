import { ImageThumbnail } from "@/components/ImageThumbnail";
import { createClient } from "@/lib/supabase/server";
import type { ImageRow } from "@/lib/supabase/types";
import Link from "next/link";

export const dynamic = "force-dynamic";

function VisibilityBadge({ v }: { v: ImageRow["visibility"] }) {
  const style =
    v === "public"
      ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200"
      : v === "lab"
        ? "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-200"
        : "bg-neutral-100 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300";
  return <span className={`rounded px-1.5 py-0.5 text-xs ${style}`}>{v}</span>;
}

export default async function DashboardPage() {
  const supabase = createClient();
  const { data: images, error } = await supabase
    .from("images")
    .select("*")
    .order("created_at", { ascending: false })
    .limit(60);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">画像一覧</h1>
        <Link
          href="/dashboard/upload"
          className="rounded bg-neutral-900 px-3 py-2 text-sm font-medium text-white hover:bg-neutral-800 dark:bg-white dark:text-neutral-900 dark:hover:bg-neutral-200"
        >
          + アップロード
        </Link>
      </div>

      {error && (
        <p className="rounded bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950 dark:text-red-200">
          読み込みエラー: {error.message}
        </p>
      )}

      {images && images.length === 0 && (
        <p className="rounded border border-dashed border-neutral-300 p-8 text-center text-sm text-neutral-500 dark:border-neutral-700">
          まだ画像がありません。右上の「アップロード」から追加してください。
        </p>
      )}

      {images && images.length > 0 && (
        <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {images.map((img) => (
            <li
              key={img.id}
              className="overflow-hidden rounded-lg border border-neutral-200 dark:border-neutral-800"
            >
              <Link href={`/dashboard/images/${img.id}`}>
                <ImageThumbnail storagePath={img.storage_path} alt={img.original_filename ?? ""} />
                <div className="space-y-1 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-sm font-medium">
                      {img.original_filename ?? img.id}
                    </span>
                    <VisibilityBadge v={img.visibility} />
                  </div>
                  <p className="text-xs text-neutral-500">
                    {new Date(img.created_at).toLocaleString("ja-JP")}
                  </p>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
