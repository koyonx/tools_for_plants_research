"use client";

import { downloadAuthed } from "@/lib/download-authed";
import { useState } from "react";

export function TrainingExportButton() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [includeUnlabelled, setIncludeUnlabelled] = useState(false);

  const onClick = async () => {
    setBusy(true);
    setError(null);
    const qs = includeUnlabelled ? "?include_unlabelled=true" : "";
    const result = await downloadAuthed(
      `/training/export.zip${qs}`,
      "plants-research-training.zip",
    );
    if (!result.ok) setError(result.error);
    setBusy(false);
  };

  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex items-center gap-2">
        <label className="flex items-center gap-1 text-xs text-neutral-500">
          <input
            type="checkbox"
            checked={includeUnlabelled}
            onChange={(e) => setIncludeUnlabelled(e.target.checked)}
          />
          未ラベルも含める
        </label>
        <button
          type="button"
          onClick={onClick}
          disabled={busy}
          className="rounded border border-neutral-300 px-3 py-2 text-sm hover:bg-neutral-100 disabled:opacity-50 dark:border-neutral-700 dark:hover:bg-neutral-900"
        >
          {busy ? "書き出し中…" : "学習データ zip"}
        </button>
      </div>
      {error && <p className="text-xs text-red-700 dark:text-red-300">{error}</p>}
    </div>
  );
}
