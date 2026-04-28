"use client";

import { errorMessage } from "@/lib/error-message";
import { createClient } from "@/lib/supabase/client";
import { TISSUE_CLASSES, type TissueClassKey } from "@/lib/tissue-classes";
import { useRouter } from "next/navigation";
import { useState } from "react";

type Polygon = [number, number][];

type Props = {
  imageId: string;
  ownerId: string;
  // Polygons in **original image pixel space**.  Caller is responsible for
  // up-scaling Cellpose's downsampled output back to the source resolution.
  polygons: Polygon[];
  // Default class shown in the dropdown.  Most plant cell-detection runs
  // are mesophyll-heavy, so "spongy" is a reasonable default.
  defaultClass?: TissueClassKey;
  // Free-form button label so callers can tailor wording per panel
  // (e.g. "セルをアノテーションに追加" vs "組織マスクをアノテーションに追加").
  label?: string;
  // Hint shown next to the dropdown.  Lets the caller explain what is
  // being sampled (e.g. "Cellpose 検出 257 件").
  countHint?: string;
};

/**
 * Bulk-insert polygons (e.g. from a Cellpose run) into `public.annotations`
 * under a single user-chosen class.  Bypasses the polygon-by-polygon
 * editor flow when a whole detection result is "good enough" to use as
 * training labels.  Outliers can be skipped by simply not pressing the
 * button on bad images.
 */
export function SampleAsAnnotations({
  imageId,
  ownerId,
  polygons,
  defaultClass = "spongy",
  label = "アノテーションに追加",
  countHint,
}: Props) {
  const supabase = createClient();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [klass, setKlass] = useState<TissueClassKey>(defaultClass);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedCount, setSavedCount] = useState<number | null>(null);

  const valid = polygons.filter((p) => Array.isArray(p) && p.length >= 3);

  const submit = async () => {
    if (valid.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      const rows = valid.map((polygon) => ({
        image_id: imageId,
        owner_id: ownerId,
        class: klass,
        polygon,
      }));
      const { error: insErr, count } = await supabase
        .from("annotations")
        .insert(rows, { count: "exact" });
      if (insErr) throw insErr;
      setSavedCount(count ?? rows.length);
      setOpen(false);
      router.refresh();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      {savedCount !== null && (
        <span className="rounded bg-green-50 px-2 py-1 text-green-800 dark:bg-green-950 dark:text-green-200">
          {savedCount} 件をアノテーションに追加しました
        </span>
      )}
      {!open ? (
        <button
          type="button"
          onClick={() => {
            setOpen(true);
            setError(null);
          }}
          disabled={valid.length === 0}
          className="rounded border border-neutral-300 px-2 py-1 text-xs hover:bg-neutral-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-neutral-700 dark:hover:bg-neutral-900"
          title={
            valid.length === 0 ? "保存できるポリゴンがありません" : "結果をアノテーションとして保存"
          }
        >
          {label}
          {countHint ? ` (${countHint})` : ""}
        </button>
      ) : (
        <>
          <label className="flex items-center gap-1">
            クラス:
            <select
              value={klass}
              onChange={(e) => setKlass(e.target.value as TissueClassKey)}
              className="rounded border border-neutral-300 bg-transparent px-1 py-0.5 dark:border-neutral-700"
            >
              {TISSUE_CLASSES.map((c) => (
                <option key={c.key} value={c.key}>
                  {c.label}
                </option>
              ))}
            </select>
          </label>
          <span className="text-neutral-500">{valid.length} 件 を追加</span>
          <button
            type="button"
            onClick={() => void submit()}
            disabled={busy}
            className="rounded bg-neutral-900 px-2 py-1 text-white disabled:opacity-50 dark:bg-white dark:text-neutral-900"
          >
            {busy ? "追加中…" : "追加する"}
          </button>
          <button
            type="button"
            onClick={() => setOpen(false)}
            disabled={busy}
            className="rounded border border-neutral-300 px-2 py-1 dark:border-neutral-700"
          >
            取消
          </button>
        </>
      )}
      {error && (
        <span className="rounded bg-red-50 px-2 py-1 text-red-800 dark:bg-red-950 dark:text-red-200">
          {error}
        </span>
      )}
    </div>
  );
}
