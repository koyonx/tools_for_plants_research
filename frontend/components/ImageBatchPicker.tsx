"use client";

import { createClient } from "@/lib/supabase/client";
import type { ImageRow, PhotosynthesisType } from "@/lib/supabase/types";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8001";

const PS_TYPES: (PhotosynthesisType | "")[] = ["", "C3", "C4", "CAM", "unknown"];
const PIPELINE_CHOICES: { key: string; label: string }[] = [
  { key: "basic_measurement", label: "基本計測" },
  { key: "segformer_tissue", label: "SegFormer 組織分割" },
  { key: "cellpose_cells", label: "Cellpose 細胞検出" },
  { key: "water_path", label: "水経路（要 SegFormer）" },
];

type Props = {
  initial: ImageRow[];
};

type SignedUrlEntry = { url: string; expiresAt: number };

/**
 * Full-featured image list.  Client-rendered so we can:
 *   - filter by species / photosynthesis_type / plant_id / treatment
 *   - multi-select with a checkbox column
 *   - kick off a batch_run against the selection
 *
 * Thumbnails reuse signed URLs fetched lazily per-row (1h TTL); we
 * cache them for the session in a `useState` map so re-filtering
 * doesn't refetch.
 */
export function ImageBatchPicker({ initial }: Props) {
  const supabase = useMemo(() => createClient(), []);
  const router = useRouter();
  const [images, setImages] = useState<ImageRow[]>(initial);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [filterSpecies, setFilterSpecies] = useState("");
  const [filterPs, setFilterPs] = useState<PhotosynthesisType | "">("");
  const [filterPlant, setFilterPlant] = useState("");
  const [filterTreatment, setFilterTreatment] = useState("");
  const [pipelines, setPipelines] = useState<Set<string>>(new Set(["basic_measurement"]));
  const [label, setLabel] = useState("");
  const [kickoffBusy, setKickoffBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [thumbs, setThumbs] = useState<Record<string, SignedUrlEntry>>({});

  // Refresh the image list after metadata edits on detail pages — keep it
  // snappy by re-pulling on focus.
  useEffect(() => {
    const refetch = async () => {
      const { data } = await supabase
        .from("images")
        .select("*")
        .order("created_at", { ascending: false })
        .limit(300);
      if (data) setImages(data as ImageRow[]);
    };
    const onFocus = () => void refetch();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [supabase]);

  const filtered = useMemo(
    () =>
      images.filter((img) => {
        if (filterSpecies && (img.species ?? "") !== filterSpecies) return false;
        if (filterPs && (img.photosynthesis_type ?? "") !== filterPs) return false;
        if (filterPlant && (img.plant_id ?? "") !== filterPlant) return false;
        if (filterTreatment && (img.treatment ?? "") !== filterTreatment) return false;
        return true;
      }),
    [images, filterSpecies, filterPs, filterPlant, filterTreatment],
  );

  const uniq = <T,>(vals: (T | null | undefined)[]): T[] =>
    Array.from(new Set(vals.filter((v): v is T => Boolean(v))));
  const speciesOptions = uniq(images.map((i) => i.species));
  const plantOptions = uniq(images.map((i) => i.plant_id));
  const treatmentOptions = uniq(images.map((i) => i.treatment));

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectAllVisible = () => setSelected(new Set(filtered.map((i) => i.id)));
  const clearSelection = () => setSelected(new Set());
  const togglePipeline = (key: string) => {
    setPipelines((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const kickOff = async () => {
    if (selected.size === 0 || pipelines.size === 0) return;
    setKickoffBusy(true);
    setError(null);
    try {
      const { data: sess } = await supabase.auth.getSession();
      if (!sess.session) throw new Error("セッションが切れました");
      const resp = await fetch(`${BACKEND_URL}/batches`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${sess.session.access_token}`,
        },
        body: JSON.stringify({
          image_ids: Array.from(selected),
          pipeline_kinds: Array.from(pipelines),
          label: label.trim() || null,
        }),
      });
      if (!resp.ok) {
        const detail = await resp.text().catch(() => "");
        throw new Error(`${resp.status}: ${detail.slice(0, 240)}`);
      }
      const body = (await resp.json()) as { id: string };
      router.push(`/dashboard/batches/${body.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setKickoffBusy(false);
    }
  };

  const thumbFor = async (path: string) => {
    const existing = thumbs[path];
    if (existing && existing.expiresAt > Date.now()) return existing.url;
    const { data } = await supabase.storage.from("images").createSignedUrl(path, 3600);
    if (!data?.signedUrl) return null;
    setThumbs((prev) => ({
      ...prev,
      [path]: { url: data.signedUrl, expiresAt: Date.now() + 3500_000 },
    }));
    return data.signedUrl;
  };

  return (
    <div className="space-y-4">
      {/* Filters */}
      <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
        <select
          value={filterSpecies}
          onChange={(e) => setFilterSpecies(e.target.value)}
          className="rounded border border-neutral-300 bg-transparent px-2 py-1 dark:border-neutral-700"
        >
          <option value="">species (any)</option>
          {speciesOptions.map((v) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </select>
        <select
          value={filterPs}
          onChange={(e) => setFilterPs(e.target.value as PhotosynthesisType | "")}
          className="rounded border border-neutral-300 bg-transparent px-2 py-1 dark:border-neutral-700"
        >
          {PS_TYPES.map((v) => (
            <option key={v} value={v}>
              {v || "photosynthesis (any)"}
            </option>
          ))}
        </select>
        <select
          value={filterPlant}
          onChange={(e) => setFilterPlant(e.target.value)}
          className="rounded border border-neutral-300 bg-transparent px-2 py-1 dark:border-neutral-700"
        >
          <option value="">plant_id (any)</option>
          {plantOptions.map((v) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </select>
        <select
          value={filterTreatment}
          onChange={(e) => setFilterTreatment(e.target.value)}
          className="rounded border border-neutral-300 bg-transparent px-2 py-1 dark:border-neutral-700"
        >
          <option value="">treatment (any)</option>
          {treatmentOptions.map((v) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </select>
      </div>

      {/* Batch control bar */}
      <div className="sticky top-0 z-10 flex flex-wrap items-center gap-2 rounded-lg border border-neutral-200 bg-white/95 p-3 text-sm backdrop-blur dark:border-neutral-800 dark:bg-neutral-950/95">
        <button
          type="button"
          onClick={selectAllVisible}
          className="rounded border border-neutral-300 px-2 py-1 hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-900"
        >
          全選択 ({filtered.length})
        </button>
        <button
          type="button"
          onClick={clearSelection}
          className="rounded border border-neutral-300 px-2 py-1 hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-900"
        >
          解除
        </button>
        <span className="text-xs text-neutral-500">{selected.size} 選択中</span>
        <div className="ml-2 flex flex-wrap items-center gap-1 border-l border-neutral-200 pl-3 dark:border-neutral-700">
          {PIPELINE_CHOICES.map((p) => (
            <label key={p.key} className="flex items-center gap-1 text-xs">
              <input
                type="checkbox"
                checked={pipelines.has(p.key)}
                onChange={() => togglePipeline(p.key)}
              />
              {p.label}
            </label>
          ))}
        </div>
        <input
          type="text"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="batch ラベル (任意)"
          className="w-48 rounded border border-neutral-300 bg-transparent px-2 py-1 text-xs dark:border-neutral-700"
        />
        <button
          type="button"
          onClick={kickOff}
          disabled={selected.size === 0 || pipelines.size === 0 || kickoffBusy}
          className="ml-auto rounded bg-neutral-900 px-3 py-1.5 font-medium text-white disabled:opacity-50 dark:bg-white dark:text-neutral-900"
        >
          {kickoffBusy ? "起動中…" : `バッチ解析 (${selected.size})`}
        </button>
        <Link
          href="/dashboard/batches"
          className="rounded border border-neutral-300 px-2 py-1 text-xs hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-900"
        >
          履歴
        </Link>
      </div>

      {error && (
        <p className="rounded bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950 dark:text-red-200">
          {error}
        </p>
      )}

      {/* Grid */}
      {filtered.length === 0 ? (
        <p className="rounded border border-dashed border-neutral-300 p-8 text-center text-sm text-neutral-500 dark:border-neutral-700">
          条件に一致する画像はありません。
        </p>
      ) : (
        <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {filtered.map((img) => (
            <ImageCard
              key={img.id}
              img={img}
              checked={selected.has(img.id)}
              onToggle={() => toggle(img.id)}
              thumbFor={thumbFor}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function ImageCard({
  img,
  checked,
  onToggle,
  thumbFor,
}: {
  img: ImageRow;
  checked: boolean;
  onToggle: () => void;
  thumbFor: (path: string) => Promise<string | null>;
}) {
  const [thumb, setThumb] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    void thumbFor(img.storage_path).then((url) => {
      if (!cancelled) setThumb(url);
    });
    return () => {
      cancelled = true;
    };
  }, [img.storage_path, thumbFor]);

  return (
    <li className="overflow-hidden rounded-lg border border-neutral-200 dark:border-neutral-800">
      <div className="relative">
        <Link href={`/dashboard/images/${img.id}`}>
          {thumb ? (
            <img
              src={thumb}
              alt={img.original_filename ?? ""}
              className="aspect-video w-full bg-neutral-50 object-cover dark:bg-neutral-900"
              loading="lazy"
            />
          ) : (
            <div className="flex aspect-video items-center justify-center bg-neutral-100 text-xs text-neutral-500 dark:bg-neutral-900" />
          )}
        </Link>
        <label className="absolute left-2 top-2 flex h-6 w-6 items-center justify-center rounded bg-white/90 shadow dark:bg-neutral-900/90">
          <input
            type="checkbox"
            checked={checked}
            onChange={onToggle}
            aria-label="select for batch"
          />
        </label>
      </div>
      <div className="space-y-0.5 p-3 text-sm">
        <div className="flex items-center justify-between gap-2">
          <Link
            href={`/dashboard/images/${img.id}`}
            className="truncate font-medium hover:underline"
          >
            {img.original_filename ?? img.id}
          </Link>
          <span
            className={`rounded px-1.5 py-0.5 text-xs ${
              img.visibility === "public"
                ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200"
                : img.visibility === "lab"
                  ? "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-200"
                  : "bg-neutral-100 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300"
            }`}
          >
            {img.visibility}
          </span>
        </div>
        <p className="flex flex-wrap gap-2 text-xs text-neutral-500">
          {img.species && <span className="font-mono">{img.species}</span>}
          {img.photosynthesis_type && (
            <span className="rounded bg-neutral-100 px-1 font-mono dark:bg-neutral-800">
              {img.photosynthesis_type}
            </span>
          )}
          {img.plant_id && <span className="font-mono">{img.plant_id}</span>}
          {img.treatment && <span className="italic">{img.treatment}</span>}
        </p>
        <p className="text-xs text-neutral-500">
          {new Date(img.created_at).toLocaleString("ja-JP")}
        </p>
      </div>
    </li>
  );
}
