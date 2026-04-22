"use client";

import { createClient } from "@/lib/supabase/client";
import type {
  GasExchangePointRow,
  GasExchangeSessionRow,
  ImageRow,
  PhotosynthesisType,
} from "@/lib/supabase/types";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8001";

const PS_TYPES: (PhotosynthesisType | "")[] = ["", "C3", "C4", "C3-C4", "CAM", "unknown"];

type Props = {
  initialSessions: GasExchangeSessionRow[];
  images: Pick<ImageRow, "species" | "photosynthesis_type" | "plant_id" | "treatment">[];
};

type FilterState = {
  plant_id: string;
  species: string;
  photosynthesis_type: string;
};

const EMPTY_FILTER: FilterState = { plant_id: "", species: "", photosynthesis_type: "" };

function uniq(values: (string | null | undefined)[]): string[] {
  const out = new Set<string>();
  for (const v of values) if (v) out.add(v);
  return Array.from(out).sort((a, b) => a.localeCompare(b));
}

export function GasExchangeDashboard({ initialSessions, images }: Props) {
  const supabase = useMemo(() => createClient(), []);
  const [sessions, setSessions] = useState(initialSessions);
  const [filter, setFilter] = useState<FilterState>(EMPTY_FILTER);
  const [overrides, setOverrides] = useState({
    label: "",
    plant_id: "",
    species: "",
    photosynthesis_type: "" as PhotosynthesisType | "",
    treatment: "",
  });
  const [uploadBusy, setUploadBusy] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadSummary, setUploadSummary] = useState<string | null>(null);
  const [selectedSession, setSelectedSession] = useState<GasExchangeSessionRow | null>(null);
  const [selectedPoints, setSelectedPoints] = useState<GasExchangePointRow[] | null>(null);
  const [detailBusy, setDetailBusy] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  // Filter dropdown options seeded from both image rows and session
  // rows so a user can also filter by labels they've only used in
  // gas-exchange uploads.
  const speciesOptions = useMemo(
    () => uniq([...images.map((i) => i.species), ...sessions.map((s) => s.species)]),
    [images, sessions],
  );
  const plantOptions = useMemo(
    () => uniq([...images.map((i) => i.plant_id), ...sessions.map((s) => s.plant_id)]),
    [images, sessions],
  );

  const filtered = useMemo(() => {
    return sessions.filter((s) => {
      if (filter.plant_id && s.plant_id !== filter.plant_id) return false;
      if (filter.species && s.species !== filter.species) return false;
      if (filter.photosynthesis_type && s.photosynthesis_type !== filter.photosynthesis_type)
        return false;
      return true;
    });
  }, [sessions, filter]);

  // Load points whenever a session is selected
  useEffect(() => {
    if (!selectedSession) {
      setSelectedPoints(null);
      return;
    }
    let cancelled = false;
    setDetailBusy(true);
    setDetailError(null);
    void (async () => {
      try {
        const { data: sess } = await supabase.auth.getSession();
        if (!sess.session) throw new Error("セッションが切れました");
        const resp = await fetch(`${BACKEND_URL}/gas-exchange/sessions/${selectedSession.id}`, {
          headers: { Authorization: `Bearer ${sess.session.access_token}` },
        });
        if (!resp.ok) {
          throw new Error(`${resp.status}: ${(await resp.text()).slice(0, 240)}`);
        }
        const body = (await resp.json()) as { points: GasExchangePointRow[] };
        if (!cancelled) setSelectedPoints(body.points);
      } catch (e) {
        if (!cancelled) setDetailError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setDetailBusy(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [supabase, selectedSession]);

  const onUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setUploadBusy(true);
    setUploadError(null);
    setUploadSummary(null);
    try {
      const { data: sess } = await supabase.auth.getSession();
      if (!sess.session) throw new Error("セッションが切れました");
      const fd = new FormData();
      fd.append("file", file);
      if (overrides.label) fd.append("label", overrides.label);
      if (overrides.plant_id) fd.append("plant_id", overrides.plant_id);
      if (overrides.species) fd.append("species", overrides.species);
      if (overrides.photosynthesis_type)
        fd.append("photosynthesis_type", overrides.photosynthesis_type);
      if (overrides.treatment) fd.append("treatment", overrides.treatment);
      const resp = await fetch(`${BACKEND_URL}/gas-exchange/upload`, {
        method: "POST",
        headers: { Authorization: `Bearer ${sess.session.access_token}` },
        body: fd,
      });
      if (!resp.ok) {
        const detail = await resp.text();
        throw new Error(`${resp.status}: ${detail.slice(0, 320)}`);
      }
      const body = (await resp.json()) as {
        session: GasExchangeSessionRow;
        point_count: number;
      };
      setSessions((prev) => [body.session, ...prev]);
      setUploadSummary(`取り込み成功: ${body.point_count} 点 (${body.session.instrument})`);
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : String(e));
    } finally {
      setUploadBusy(false);
      // Allow re-upload of the same file
      event.target.value = "";
    }
  };

  const onDelete = async (session: GasExchangeSessionRow) => {
    if (
      !confirm(
        `セッション "${session.label ?? session.file_name ?? session.id.slice(0, 8)}" を削除しますか？`,
      )
    )
      return;
    try {
      const { data: sess } = await supabase.auth.getSession();
      if (!sess.session) throw new Error("セッションが切れました");
      const resp = await fetch(`${BACKEND_URL}/gas-exchange/sessions/${session.id}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${sess.session.access_token}` },
      });
      if (!resp.ok) throw new Error(`${resp.status}: ${await resp.text()}`);
      setSessions((prev) => prev.filter((s) => s.id !== session.id));
      if (selectedSession?.id === session.id) setSelectedSession(null);
    } catch (e) {
      alert(`削除失敗: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  return (
    <div className="space-y-6">
      <UploadCard
        overrides={overrides}
        setOverrides={setOverrides}
        plantOptions={plantOptions}
        speciesOptions={speciesOptions}
        busy={uploadBusy}
        error={uploadError}
        summary={uploadSummary}
        onUpload={onUpload}
      />
      <FilterBar
        filter={filter}
        setFilter={setFilter}
        plantOptions={plantOptions}
        speciesOptions={speciesOptions}
      />
      <SessionsTable
        sessions={filtered}
        onSelect={setSelectedSession}
        onDelete={onDelete}
        selectedId={selectedSession?.id ?? null}
      />
      {selectedSession && (
        <SessionDetail
          session={selectedSession}
          points={selectedPoints}
          busy={detailBusy}
          error={detailError}
          onClose={() => setSelectedSession(null)}
        />
      )}
    </div>
  );
}

function UploadCard({
  overrides,
  setOverrides,
  plantOptions,
  speciesOptions,
  busy,
  error,
  summary,
  onUpload,
}: {
  overrides: {
    label: string;
    plant_id: string;
    species: string;
    photosynthesis_type: PhotosynthesisType | "";
    treatment: string;
  };
  setOverrides: React.Dispatch<React.SetStateAction<typeof overrides>>;
  plantOptions: string[];
  speciesOptions: string[];
  busy: boolean;
  error: string | null;
  summary: string | null;
  onUpload: (event: React.ChangeEvent<HTMLInputElement>) => void;
}) {
  return (
    <section className="space-y-3 rounded-lg border border-neutral-200 p-4 dark:border-neutral-800">
      <h2 className="text-lg font-semibold">ファイル取り込み</h2>
      <p className="text-xs text-neutral-500">
        LI-6400 / LI-6800 の .xlsx か、CSV / TSV
        をアップロード。機種と列レイアウトは自動判定します。 plant_id / species /
        photosynthesis_type を指定すると画像メタデータと結合できます（任意）。
      </p>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <FieldText
          label="ラベル (任意)"
          value={overrides.label}
          onChange={(v) => setOverrides((p) => ({ ...p, label: v }))}
        />
        <FieldList
          label="plant_id"
          value={overrides.plant_id}
          options={plantOptions}
          onChange={(v) => setOverrides((p) => ({ ...p, plant_id: v }))}
        />
        <FieldList
          label="species"
          value={overrides.species}
          options={speciesOptions}
          onChange={(v) => setOverrides((p) => ({ ...p, species: v }))}
        />
        <FieldSelect
          label="photosynthesis_type"
          value={overrides.photosynthesis_type}
          options={PS_TYPES}
          onChange={(v) =>
            setOverrides((p) => ({
              ...p,
              photosynthesis_type: v as PhotosynthesisType | "",
            }))
          }
        />
        <FieldText
          label="treatment"
          value={overrides.treatment}
          onChange={(v) => setOverrides((p) => ({ ...p, treatment: v }))}
        />
      </div>
      <label className="inline-flex cursor-pointer items-center gap-3 rounded border border-dashed border-neutral-400 px-4 py-3 text-sm hover:bg-neutral-50 dark:hover:bg-neutral-900">
        <input
          type="file"
          accept=".xlsx,.csv,.tsv,.txt"
          disabled={busy}
          onChange={onUpload}
          className="hidden"
        />
        {busy ? "取り込み中…" : "ファイルを選択して取り込み"}
      </label>
      {summary && (
        <p className="rounded bg-emerald-50 p-3 text-sm text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200">
          {summary}
        </p>
      )}
      {error && (
        <p className="rounded bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950 dark:text-red-200">
          {error}
        </p>
      )}
    </section>
  );
}

function FilterBar({
  filter,
  setFilter,
  plantOptions,
  speciesOptions,
}: {
  filter: FilterState;
  setFilter: React.Dispatch<React.SetStateAction<FilterState>>;
  plantOptions: string[];
  speciesOptions: string[];
}) {
  return (
    <section className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      <FieldList
        label="plant_id で絞り込み"
        value={filter.plant_id}
        options={plantOptions}
        onChange={(v) => setFilter((p) => ({ ...p, plant_id: v }))}
      />
      <FieldList
        label="species で絞り込み"
        value={filter.species}
        options={speciesOptions}
        onChange={(v) => setFilter((p) => ({ ...p, species: v }))}
      />
      <FieldSelect
        label="C3/C4 で絞り込み"
        value={filter.photosynthesis_type}
        options={PS_TYPES}
        onChange={(v) => setFilter((p) => ({ ...p, photosynthesis_type: v }))}
      />
    </section>
  );
}

function SessionsTable({
  sessions,
  onSelect,
  onDelete,
  selectedId,
}: {
  sessions: GasExchangeSessionRow[];
  onSelect: (s: GasExchangeSessionRow) => void;
  onDelete: (s: GasExchangeSessionRow) => void;
  selectedId: string | null;
}) {
  if (sessions.length === 0) {
    return (
      <p className="rounded bg-neutral-50 p-3 text-sm text-neutral-600 dark:bg-neutral-900 dark:text-neutral-400">
        取り込み済みセッションはありません。
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-sm">
        <thead className="bg-neutral-100 text-xs uppercase text-neutral-500 dark:bg-neutral-900">
          <tr>
            <th className="px-3 py-2 text-left">取得日時</th>
            <th className="px-3 py-2 text-left">機種</th>
            <th className="px-3 py-2 text-left">plant_id / species</th>
            <th className="px-3 py-2 text-left">treatment</th>
            <th className="px-3 py-2 text-left">点数</th>
            <th className="px-3 py-2 text-left">ラベル / ファイル名</th>
            <th className="px-3 py-2 text-right">操作</th>
          </tr>
        </thead>
        <tbody>
          {sessions.map((s) => (
            <tr
              key={s.id}
              className={`border-b border-neutral-200 dark:border-neutral-800 ${
                s.id === selectedId ? "bg-amber-50 dark:bg-amber-950" : ""
              }`}
            >
              <td className="px-3 py-2 font-mono text-xs">
                {s.captured_at
                  ? new Date(s.captured_at).toLocaleString("ja-JP")
                  : new Date(s.created_at).toLocaleString("ja-JP")}
              </td>
              <td className="px-3 py-2 font-mono">{s.instrument}</td>
              <td className="px-3 py-2">
                {s.plant_id ?? "—"}
                {s.species ? ` / ${s.species}` : ""}
                {s.photosynthesis_type ? ` (${s.photosynthesis_type})` : ""}
              </td>
              <td className="px-3 py-2">{s.treatment ?? "—"}</td>
              <td className="px-3 py-2 text-right font-mono">{s.point_count}</td>
              <td className="px-3 py-2 text-xs">{s.label ?? s.file_name ?? s.id.slice(0, 8)}</td>
              <td className="px-3 py-2 text-right">
                <button
                  type="button"
                  onClick={() => onSelect(s)}
                  className="rounded border border-neutral-300 px-2 py-1 text-xs hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-900"
                >
                  詳細
                </button>{" "}
                <button
                  type="button"
                  onClick={() => onDelete(s)}
                  className="rounded border border-red-300 px-2 py-1 text-xs text-red-700 hover:bg-red-50 dark:border-red-800 dark:text-red-300 dark:hover:bg-red-950"
                >
                  削除
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SessionDetail({
  session,
  points,
  busy,
  error,
  onClose,
}: {
  session: GasExchangeSessionRow;
  points: GasExchangePointRow[] | null;
  busy: boolean;
  error: string | null;
  onClose: () => void;
}) {
  return (
    <section className="space-y-3 rounded-lg border-2 border-amber-300 p-4 dark:border-amber-900">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">
            {session.label ?? session.file_name ?? session.id.slice(0, 12)}
          </h2>
          <p className="text-xs text-neutral-500">
            {session.instrument} / {session.point_count} 点 / 取得:{" "}
            {session.captured_at ? new Date(session.captured_at).toLocaleString("ja-JP") : "—"}
          </p>
        </div>
        <button type="button" onClick={onClose} className="text-sm text-neutral-500 underline">
          閉じる
        </button>
      </div>
      {session.plant_id && (
        <p className="text-xs text-neutral-500">
          同一 plant_id の画像を見る:{" "}
          <Link
            href={`/dashboard?plant_id=${encodeURIComponent(session.plant_id)}`}
            className="underline"
          >
            /dashboard?plant_id={session.plant_id}
          </Link>
        </p>
      )}
      {error && (
        <p className="rounded bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950 dark:text-red-200">
          {error}
        </p>
      )}
      {busy && <p className="text-sm text-neutral-500">読み込み中…</p>}
      {!busy && points && points.length > 0 && (
        <>
          <ACiScatter points={points} />
          <PointsTable points={points.slice(0, 50)} />
          {points.length > 50 && (
            <p className="text-xs text-neutral-500">
              {points.length - 50} 件の追加データはダウンロード API 経由で取得可能。
            </p>
          )}
        </>
      )}
    </section>
  );
}

function ACiScatter({ points }: { points: GasExchangePointRow[] }) {
  const valid = points.filter(
    (p) =>
      p.ci_ppm !== null &&
      p.photo_a !== null &&
      Number.isFinite(p.ci_ppm) &&
      Number.isFinite(p.photo_a),
  ) as (GasExchangePointRow & { ci_ppm: number; photo_a: number })[];
  if (valid.length === 0) {
    return <p className="text-xs text-neutral-500">A/Ci プロット用のデータが不足しています。</p>;
  }
  const xs = valid.map((p) => p.ci_ppm);
  const ys = valid.map((p) => p.photo_a);
  const xmin = Math.min(...xs);
  const xmax = Math.max(...xs);
  const ymin = Math.min(...ys);
  const ymax = Math.max(...ys);
  const w = 480;
  const h = 240;
  const pad = 32;
  const sx = (x: number) => pad + ((x - xmin) / Math.max(xmax - xmin, 1e-6)) * (w - 2 * pad);
  const sy = (y: number) => h - pad - ((y - ymin) / Math.max(ymax - ymin, 1e-6)) * (h - 2 * pad);
  return (
    <div className="overflow-x-auto">
      <svg
        width={w}
        height={h}
        className="rounded border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-950"
        aria-label="A/Ci scatter plot"
      >
        <title>A vs Ci ({valid.length} points)</title>
        <text x={pad} y={16} className="fill-neutral-500" fontSize="11">
          A (µmol m⁻² s⁻¹)
        </text>
        <text x={w - pad} y={h - 8} textAnchor="end" className="fill-neutral-500" fontSize="11">
          Ci (µmol mol⁻¹)
        </text>
        <line x1={pad} y1={h - pad} x2={w - pad} y2={h - pad} stroke="#888" strokeWidth={1} />
        <line x1={pad} y1={pad} x2={pad} y2={h - pad} stroke="#888" strokeWidth={1} />
        {valid.map((p) => (
          <circle
            key={p.id}
            cx={sx(p.ci_ppm)}
            cy={sy(p.photo_a)}
            r={3}
            fill="rgba(56,189,248,0.85)"
          />
        ))}
      </svg>
    </div>
  );
}

function fmt(n: number | null, digits = 2): string {
  if (n === null || !Number.isFinite(n)) return "—";
  return n.toFixed(digits);
}

function PointsTable({ points }: { points: GasExchangePointRow[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-xs">
        <thead className="bg-neutral-100 text-neutral-500 dark:bg-neutral-900">
          <tr>
            <th className="px-2 py-1 text-left">obs</th>
            <th className="px-2 py-1 text-left">時刻</th>
            <th className="px-2 py-1 text-right">A</th>
            <th className="px-2 py-1 text-right">gsw</th>
            <th className="px-2 py-1 text-right">Ci</th>
            <th className="px-2 py-1 text-right">CO₂_r</th>
            <th className="px-2 py-1 text-right">VPD</th>
            <th className="px-2 py-1 text-right">Tleaf</th>
            <th className="px-2 py-1 text-right">PAR</th>
            <th className="px-2 py-1 text-right">E</th>
          </tr>
        </thead>
        <tbody>
          {points.map((p) => (
            <tr key={p.id} className="border-b border-neutral-200 dark:border-neutral-800">
              <td className="px-2 py-1 font-mono">{p.obs_index}</td>
              <td className="px-2 py-1 font-mono">
                {p.recorded_at ? new Date(p.recorded_at).toLocaleTimeString("ja-JP") : "—"}
              </td>
              <td className="px-2 py-1 text-right font-mono">{fmt(p.photo_a)}</td>
              <td className="px-2 py-1 text-right font-mono">{fmt(p.cond_gsw, 4)}</td>
              <td className="px-2 py-1 text-right font-mono">{fmt(p.ci_ppm, 1)}</td>
              <td className="px-2 py-1 text-right font-mono">{fmt(p.co2_ref_ppm, 1)}</td>
              <td className="px-2 py-1 text-right font-mono">{fmt(p.vpd_kpa)}</td>
              <td className="px-2 py-1 text-right font-mono">{fmt(p.leaf_temp_c)}</td>
              <td className="px-2 py-1 text-right font-mono">{fmt(p.par_umol, 0)}</td>
              <td className="px-2 py-1 text-right font-mono">{fmt(p.transpiration)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FieldText({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <label className="block text-xs text-neutral-500">
      <span className="mb-1 block">{label}</span>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded border border-neutral-300 bg-white px-2 py-1 text-sm dark:border-neutral-700 dark:bg-neutral-900"
      />
    </label>
  );
}

function FieldSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: readonly string[];
  onChange: (v: string) => void;
}) {
  return (
    <label className="block text-xs text-neutral-500">
      <span className="mb-1 block">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded border border-neutral-300 bg-white px-2 py-1 text-sm dark:border-neutral-700 dark:bg-neutral-900"
      >
        {options.map((o) => (
          <option key={o || "_blank"} value={o}>
            {o || "（指定なし）"}
          </option>
        ))}
      </select>
    </label>
  );
}

function FieldList({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: readonly string[];
  onChange: (v: string) => void;
}) {
  // Datalist gives us free-text + suggestions in one control.
  const id = `${label}-${Math.random().toString(36).slice(2)}`;
  return (
    <label className="block text-xs text-neutral-500">
      <span className="mb-1 block">{label}</span>
      <input
        type="text"
        value={value}
        list={id}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded border border-neutral-300 bg-white px-2 py-1 text-sm dark:border-neutral-700 dark:bg-neutral-900"
      />
      <datalist id={id}>
        {options.map((o) => (
          <option key={o} value={o} />
        ))}
      </datalist>
    </label>
  );
}
