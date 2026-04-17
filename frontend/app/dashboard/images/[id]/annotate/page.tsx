import { AnnotationEditor } from "@/components/AnnotationEditor";
import { toPublicSupabaseUrl } from "@/lib/supabase/public-url";
import { createClient } from "@/lib/supabase/server";
import type { AnnotationRow, ImageRow } from "@/lib/supabase/types";
import Link from "next/link";
import { notFound, redirect } from "next/navigation";

export const dynamic = "force-dynamic";

export default async function AnnotatePage({
  params,
}: {
  params: { id: string };
}) {
  const supabase = createClient();

  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data: image, error: imgErr } = await supabase
    .from("images")
    .select("*")
    .eq("id", params.id)
    .maybeSingle<ImageRow>();

  if (imgErr) {
    return (
      <p className="rounded bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950 dark:text-red-200">
        読み込みエラー: {imgErr.message}
      </p>
    );
  }
  if (!image) notFound();

  if (!image.width_px || !image.height_px) {
    return (
      <div className="space-y-3">
        <Link href={`/dashboard/images/${image.id}`} className="text-sm text-neutral-500 underline">
          ← 画像ページに戻る
        </Link>
        <p className="rounded bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-200">
          この画像はサイズが記録されていないためアノテーションできません。再アップロードしてください。
        </p>
      </div>
    );
  }

  const { data: signed } = await supabase.storage
    .from("images")
    .createSignedUrl(image.storage_path, 60 * 60);

  const { data: annotations } = await supabase
    .from("annotations")
    .select("*")
    .eq("image_id", image.id)
    .order("created_at", { ascending: true });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <Link
            href={`/dashboard/images/${image.id}`}
            className="text-sm text-neutral-500 underline"
          >
            ← 画像ページに戻る
          </Link>
          <h1 className="mt-2 text-xl font-semibold">
            アノテーション: {image.original_filename ?? image.id}
          </h1>
          <p className="text-xs text-neutral-500">
            学習データ用の手動ポリゴンラベル。PR #5 で深層学習の訓練に使用。
          </p>
        </div>
      </div>

      {signed?.signedUrl ? (
        <AnnotationEditor
          imageId={image.id}
          imageUrl={toPublicSupabaseUrl(signed.signedUrl)}
          imageWidth={image.width_px}
          imageHeight={image.height_px}
          initial={(annotations ?? []) as AnnotationRow[]}
          currentUserId={user.id}
          canEdit
        />
      ) : (
        <p className="rounded bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-200">
          画像 URL を取得できませんでした。権限を確認してください。
        </p>
      )}
    </div>
  );
}
