"use client";

import { createClient } from "@/lib/supabase/client";
import type { ImageRow, PhotosynthesisType } from "@/lib/supabase/types";
import { useState } from "react";

type Props = {
  image: ImageRow;
  canEdit: boolean;
};

const PS_TYPES: PhotosynthesisType[] = ["C3", "C4", "C3-C4", "CAM", "unknown"];

/**
 * Inline editor for study-grouping fields (species / photosynthesis_type /
 * plant_id / treatment).  Saves on blur so the user doesn't have to hit a
 * "save" button for every field.
 */
export function ImageMetadataEditor({ image, canEdit }: Props) {
  const supabase = createClient();
  const [species, setSpecies] = useState(image.species ?? "");
  const [psType, setPsType] = useState<PhotosynthesisType | "">(image.photosynthesis_type ?? "");
  const [plantId, setPlantId] = useState(image.plant_id ?? "");
  const [treatment, setTreatment] = useState(image.treatment ?? "");
  const [savingField, setSavingField] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const save = async (field: string, value: string | null) => {
    if (!canEdit) return;
    setSavingField(field);
    setError(null);
    try {
      const patch: Record<string, string | null> = { [field]: value };
      const { error: updErr } = await supabase.from("images").update(patch).eq("id", image.id);
      if (updErr) throw updErr;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSavingField(null);
    }
  };

  return (
    <section className="space-y-3 rounded-lg border border-neutral-200 p-4 dark:border-neutral-800">
      <h2 className="text-lg font-semibold">研究メタデータ</h2>
      <div className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
        <label className="flex flex-col gap-1">
          <span className="text-neutral-500">species</span>
          <input
            type="text"
            value={species}
            disabled={!canEdit}
            onChange={(e) => setSpecies(e.target.value)}
            onBlur={() => save("species", species.trim() || null)}
            placeholder="e.g. Zea mays, Triticum aestivum"
            className="rounded border border-neutral-300 bg-transparent px-2 py-1 disabled:opacity-60 dark:border-neutral-700"
          />
        </label>

        <label className="flex flex-col gap-1">
          <span className="text-neutral-500">photosynthesis_type</span>
          <select
            value={psType}
            disabled={!canEdit}
            onChange={(e) => {
              const v = e.target.value as PhotosynthesisType | "";
              setPsType(v);
              void save("photosynthesis_type", v || null);
            }}
            className="rounded border border-neutral-300 bg-transparent px-2 py-1 disabled:opacity-60 dark:border-neutral-700"
          >
            <option value="">—</option>
            {PS_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-1">
          <span className="text-neutral-500">plant_id</span>
          <input
            type="text"
            value={plantId}
            disabled={!canEdit}
            onChange={(e) => setPlantId(e.target.value)}
            onBlur={() => save("plant_id", plantId.trim() || null)}
            placeholder="e.g. plantA-rep1"
            className="rounded border border-neutral-300 bg-transparent px-2 py-1 disabled:opacity-60 dark:border-neutral-700"
          />
        </label>

        <label className="flex flex-col gap-1">
          <span className="text-neutral-500">treatment</span>
          <input
            type="text"
            value={treatment}
            disabled={!canEdit}
            onChange={(e) => setTreatment(e.target.value)}
            onBlur={() => save("treatment", treatment.trim() || null)}
            placeholder="e.g. control, drought, 1500 µmol PAR"
            className="rounded border border-neutral-300 bg-transparent px-2 py-1 disabled:opacity-60 dark:border-neutral-700"
          />
        </label>
      </div>
      <p className="text-xs text-neutral-500">
        値はフォーカスを外した時点で保存されます。
        {savingField && ` · 保存中 (${savingField})…`}
      </p>
      {error && (
        <p className="rounded bg-red-50 p-2 text-xs text-red-800 dark:bg-red-950 dark:text-red-200">
          保存失敗: {error}
        </p>
      )}
    </section>
  );
}
