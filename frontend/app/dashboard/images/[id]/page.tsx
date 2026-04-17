import { AnalyzePanel } from "@/components/AnalyzePanel";
import { ImageViewer } from "@/components/ImageViewer";
import { toPublicSupabaseUrl } from "@/lib/supabase/public-url";
import { createClient } from "@/lib/supabase/server";
import type { AnalysisRow, ImageRow } from "@/lib/supabase/types";
import Link from "next/link";
import { notFound } from "next/navigation";

export const dynamic = "force-dynamic";

export default async function ImageDetailPage({
  params,
}: {
  params: { id: string };
}) {
  const supabase = createClient();
  const { data: image, error } = await supabase
    .from("images")
    .select("*")
    .eq("id", params.id)
    .maybeSingle<ImageRow>();

  if (error) {
    return (
      <p className="rounded bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950 dark:text-red-200">
        読み込みエラー: {error.message}
      </p>
    );
  }
  if (!image) notFound();

  const { data: signed } = await supabase.storage
    .from("images")
    .createSignedUrl(image.storage_path, 60 * 60);

  const { data: latestAnalysis } = await supabase
    .from("analyses")
    .select("*")
    .eq("image_id", image.id)
    .eq("kind", "basic_measurement")
    .order("created_at", { ascending: false })
    .limit(1)
    .maybeSingle<AnalysisRow>();

  const {
    data: { user },
  } = await supabase.auth.getUser();
  const isOwner = Boolean(user && user.id === image.owner_id);

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <Link href="/dashboard" className="text-sm text-neutral-500 underline">
            ← 一覧へ戻る
          </Link>
          <h1 className="mt-2 text-xl font-semibold">{image.original_filename ?? image.id}</h1>
          <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs text-neutral-500">
            <dt>公開範囲</dt>
            <dd className="font-mono">{image.visibility}</dd>
            <dt>サイズ</dt>
            <dd>
              {image.width_px && image.height_px
                ? `${image.width_px} × ${image.height_px} px`
                : "未取得"}
            </dd>
            <dt>スケール</dt>
            <dd>
              {image.scale_um_per_px
                ? `${image.scale_um_per_px.toFixed(4)} µm/px`
                : "未キャリブレーション"}
            </dd>
            <dt>アップロード</dt>
            <dd>{new Date(image.created_at).toLocaleString("ja-JP")}</dd>
          </dl>
        </div>
        <Link
          href={`/dashboard/images/${image.id}/annotate`}
          className="rounded border border-neutral-300 px-3 py-1.5 text-sm hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-900"
        >
          アノテーション
        </Link>
      </div>

      {signed?.signedUrl ? (
        <ImageViewer
          src={toPublicSupabaseUrl(signed.signedUrl)}
          alt={image.original_filename ?? image.id}
        />
      ) : (
        <p className="rounded bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-200">
          画像の署名付き URL を取得できませんでした。権限を確認してください。
        </p>
      )}

      <AnalyzePanel imageId={image.id} initial={latestAnalysis ?? null} canRun={isOwner} />
    </div>
  );
}
