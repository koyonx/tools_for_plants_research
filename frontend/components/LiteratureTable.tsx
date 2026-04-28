"use client";

import { errorMessage } from "@/lib/error-message";
import type { LiteratureRangeRow } from "@/lib/supabase/types";
import { useEffect, useMemo, useState } from "react";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8001";

const APPLIES_TO_LABEL: Record<string, string> = {
  C3: "C3",
  C4: "C4",
  "C3-C4": "C3-C4 中間",
  CAM: "CAM",
  any: "共通",
};

function fmtNumber(n: number): string {
  if (!Number.isFinite(n)) return "—";
  if (Math.abs(n) >= 1000 || (Math.abs(n) < 0.01 && n !== 0)) {
    return n.toExponential(3);
  }
  return n.toPrecision(4);
}

export function LiteratureTable() {
  const [rows, setRows] = useState<LiteratureRangeRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    fetch(`${BACKEND_URL}/literature/ranges`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data?.ranges) {
          setRows(data.ranges as LiteratureRangeRow[]);
        } else {
          setError("文献範囲カタログの取得に失敗しました。");
        }
      })
      .catch((e) => setError(errorMessage(e)));
  }, []);

  const filtered = useMemo(() => {
    if (!rows) return [];
    const q = filter.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter(
      (r) =>
        r.parameter_key.toLowerCase().includes(q) ||
        r.source.toLowerCase().includes(q) ||
        r.applies_to.toLowerCase().includes(q),
    );
  }, [rows, filter]);

  if (error) {
    return (
      <p className="rounded bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950 dark:text-red-200">
        {error}
      </p>
    );
  }

  if (!rows) {
    return <p className="text-sm text-neutral-500">読み込み中…</p>;
  }

  return (
    <div className="space-y-3">
      <label className="block text-xs text-neutral-500">
        <span className="mb-1 block">
          フィルター (parameter_key / source / photosynthesis_type)
        </span>
        <input
          type="text"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="例: co2_s_mes_s, Tosens, C3"
          className="w-full max-w-md rounded border border-neutral-300 bg-white px-2 py-1 text-sm dark:border-neutral-700 dark:bg-neutral-900"
        />
      </label>
      <div className="overflow-x-auto">
        <table className="min-w-full text-xs">
          <thead className="bg-neutral-100 text-neutral-500 dark:bg-neutral-900">
            <tr>
              <th className="px-3 py-2 text-left">パラメータ</th>
              <th className="px-3 py-2 text-left">対象</th>
              <th className="px-3 py-2 text-right">min</th>
              <th className="px-3 py-2 text-right">typical</th>
              <th className="px-3 py-2 text-right">max</th>
              <th className="px-3 py-2 text-left">単位</th>
              <th className="px-3 py-2 text-left">出典</th>
              <th className="px-3 py-2 text-left">備考</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((r, i) => (
              <tr
                key={`${r.parameter_key}-${r.applies_to}-${i}`}
                className="border-b border-neutral-200 dark:border-neutral-800"
              >
                <td className="px-3 py-2 font-mono">{r.parameter_key}</td>
                <td className="px-3 py-2">{APPLIES_TO_LABEL[r.applies_to] ?? r.applies_to}</td>
                <td className="px-3 py-2 text-right font-mono">{fmtNumber(r.min)}</td>
                <td className="px-3 py-2 text-right font-mono">{fmtNumber(r.typical)}</td>
                <td className="px-3 py-2 text-right font-mono">{fmtNumber(r.max)}</td>
                <td className="px-3 py-2 font-mono">{r.unit}</td>
                <td className="px-3 py-2">{r.source}</td>
                <td className="px-3 py-2 text-neutral-500">{r.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-neutral-500">
        全 {rows.length} 行中 {filtered.length} 行表示
      </p>
    </div>
  );
}
