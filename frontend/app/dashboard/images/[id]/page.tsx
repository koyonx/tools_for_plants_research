import { AnalyzePanel } from "@/components/AnalyzePanel";
import { CellposePanel } from "@/components/CellposePanel";
import { Co2DiffusionPanel } from "@/components/Co2DiffusionPanel";
import { Co2MorphometricsPanel } from "@/components/Co2MorphometricsPanel";
import { DarcyPanel } from "@/components/DarcyPanel";
import { ImageMetadataEditor } from "@/components/ImageMetadataEditor";
import { ImageViewer } from "@/components/ImageViewer";
import { SegFormerPanel } from "@/components/SegFormerPanel";
import { ValidationBadge } from "@/components/ValidationBadge";
import { WaterPathPanel } from "@/components/WaterPathPanel";
import { toPublicSupabaseUrl } from "@/lib/supabase/public-url";
import { createClient } from "@/lib/supabase/server";
import type {
  AnalysisRow,
  BasicMeasurementResult,
  GasExchangeSessionRow,
  ImageRow,
} from "@/lib/supabase/types";
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

  const { data: latestCellpose } = await supabase
    .from("analyses")
    .select("*")
    .eq("image_id", image.id)
    .eq("kind", "cellpose_cells")
    .order("created_at", { ascending: false })
    .limit(1)
    .maybeSingle<AnalysisRow>();

  const { data: latestSegformer } = await supabase
    .from("analyses")
    .select("*")
    .eq("image_id", image.id)
    .eq("kind", "segformer_tissue")
    .order("created_at", { ascending: false })
    .limit(1)
    .maybeSingle<AnalysisRow>();

  const { data: latestWaterPath } = await supabase
    .from("analyses")
    .select("*")
    .eq("image_id", image.id)
    .eq("kind", "water_path")
    .order("created_at", { ascending: false })
    .limit(1)
    .maybeSingle<AnalysisRow>();

  const { data: latestCo2Morph } = await supabase
    .from("analyses")
    .select("*")
    .eq("image_id", image.id)
    .eq("kind", "co2_morphometrics")
    .order("created_at", { ascending: false })
    .limit(1)
    .maybeSingle<AnalysisRow>();

  const { data: latestDarcy } = await supabase
    .from("analyses")
    .select("*")
    .eq("image_id", image.id)
    .eq("kind", "darcy_flow")
    .order("created_at", { ascending: false })
    .limit(1)
    .maybeSingle<AnalysisRow>();

  const { data: latestCo2Diffusion } = await supabase
    .from("analyses")
    .select("*")
    .eq("image_id", image.id)
    .eq("kind", "co2_diffusion")
    .order("created_at", { ascending: false })
    .limit(1)
    .maybeSingle<AnalysisRow>();

  // PR #11: link to LI-COR gas-exchange sessions captured on the same
  // plant_id so users can jump from morphology to physiology in one
  // click.  Skipped silently when image.plant_id is null.
  const gasExchangeSessions: GasExchangeSessionRow[] = image.plant_id
    ? (((
        await supabase
          .from("gas_exchange_sessions")
          .select("id, label, instrument, captured_at, point_count, file_name")
          .eq("plant_id", image.plant_id)
          .order("captured_at", { ascending: false })
          .limit(20)
      ).data ?? []) as GasExchangeSessionRow[])
    : [];

  const {
    data: { user },
  } = await supabase.auth.getUser();
  const isOwner = Boolean(user && user.id === image.owner_id);

  // Prefer the most recent basic-measurement scale if present so Cellpose
  // cell areas can be rendered in µm² rather than raw pixels².
  const scaleResult =
    latestAnalysis?.result &&
    typeof latestAnalysis.result === "object" &&
    "scale" in latestAnalysis.result
      ? ((latestAnalysis.result as BasicMeasurementResult).scale?.um_per_px ?? null)
      : null;
  const umPerPx = image.scale_um_per_px ?? scaleResult;

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
        <div className="flex flex-col items-end gap-2">
          <Link
            href={`/dashboard/images/${image.id}/annotate`}
            className="rounded border border-neutral-300 px-3 py-1.5 text-sm hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-900"
          >
            アノテーション
          </Link>
          <ValidationBadge target={{ kind: "image", imageId: image.id }} />
        </div>
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

      <ImageMetadataEditor image={image} canEdit={isOwner} />
      <AnalyzePanel imageId={image.id} initial={latestAnalysis ?? null} canRun={isOwner} />
      {signed?.signedUrl && (
        <CellposePanel
          imageId={image.id}
          imageUrl={toPublicSupabaseUrl(signed.signedUrl)}
          initial={latestCellpose ?? null}
          umPerPx={umPerPx}
          canRun={isOwner}
        />
      )}
      {signed?.signedUrl && (
        <SegFormerPanel
          imageId={image.id}
          imageUrl={toPublicSupabaseUrl(signed.signedUrl)}
          initial={latestSegformer ?? null}
          umPerPx={umPerPx}
          canRun={isOwner}
        />
      )}
      {signed?.signedUrl && image.width_px && image.height_px && (
        <WaterPathPanel
          imageId={image.id}
          imageUrl={toPublicSupabaseUrl(signed.signedUrl)}
          initial={latestWaterPath ?? null}
          hasSegformerResult={Boolean(
            latestSegformer && latestSegformer.status === "done" && latestSegformer.result,
          )}
          canRun={isOwner}
          imageWidth={image.width_px}
          imageHeight={image.height_px}
        />
      )}
      {signed?.signedUrl && image.width_px && image.height_px && (
        <DarcyPanel
          imageId={image.id}
          imageUrl={toPublicSupabaseUrl(signed.signedUrl)}
          initial={latestDarcy ?? null}
          hasSegformerResult={Boolean(
            latestSegformer && latestSegformer.status === "done" && latestSegformer.result,
          )}
          canRun={isOwner}
          imageWidth={image.width_px}
          imageHeight={image.height_px}
        />
      )}
      {signed?.signedUrl && image.width_px && image.height_px && (
        <Co2MorphometricsPanel
          imageId={image.id}
          imageUrl={toPublicSupabaseUrl(signed.signedUrl)}
          initial={latestCo2Morph ?? null}
          hasSegformerResult={Boolean(
            latestSegformer && latestSegformer.status === "done" && latestSegformer.result,
          )}
          hasCellposeResult={Boolean(
            latestCellpose && latestCellpose.status === "done" && latestCellpose.result,
          )}
          canRun={isOwner}
          imageWidth={image.width_px}
          imageHeight={image.height_px}
        />
      )}
      {signed?.signedUrl && image.width_px && image.height_px && (
        <Co2DiffusionPanel
          imageId={image.id}
          imageUrl={toPublicSupabaseUrl(signed.signedUrl)}
          initial={latestCo2Diffusion ?? null}
          hasSegformerResult={Boolean(
            latestSegformer && latestSegformer.status === "done" && latestSegformer.result,
          )}
          hasCo2MorphResult={Boolean(
            latestCo2Morph && latestCo2Morph.status === "done" && latestCo2Morph.result,
          )}
          canRun={isOwner}
          imageWidth={image.width_px}
          imageHeight={image.height_px}
        />
      )}
      {gasExchangeSessions.length > 0 && (
        <section className="space-y-2 rounded-lg border border-neutral-200 p-4 dark:border-neutral-800">
          <h2 className="text-lg font-semibold">関連 LI-COR ガス交換セッション</h2>
          <p className="text-xs text-neutral-500">
            同一 plant_id (<code className="font-mono">{image.plant_id}</code>)
            で取り込まれたセッション。
          </p>
          <ul className="space-y-1 text-sm">
            {gasExchangeSessions.map((s) => (
              <li key={s.id} className="font-mono text-xs">
                <Link href={`/dashboard/gas-exchange?session=${s.id}`} className="underline">
                  {s.captured_at
                    ? new Date(s.captured_at).toLocaleString("ja-JP")
                    : s.id.slice(0, 8)}
                </Link>{" "}
                · {s.instrument} · {s.point_count} 点
                {s.label || s.file_name ? ` · ${s.label ?? s.file_name}` : ""}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
