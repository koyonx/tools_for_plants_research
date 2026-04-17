"use client";

import { downloadAuthed } from "@/lib/download-authed";
import { useState } from "react";

export function MaskDownloadButton({ imageId }: { imageId: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onClick = async () => {
    setBusy(true);
    setError(null);
    const result = await downloadAuthed(`/images/${imageId}/mask.png`, `mask_${imageId}.png`);
    if (!result.ok) setError(result.error);
    setBusy(false);
  };

  return (
    <div className="flex flex-col items-start gap-1">
      <button
        type="button"
        onClick={onClick}
        disabled={busy}
        className="rounded border border-neutral-300 px-3 py-1.5 text-xs hover:bg-neutral-100 disabled:opacity-50 dark:border-neutral-700 dark:hover:bg-neutral-900"
      >
        {busy ? "書き出し中…" : "マスクをダウンロード (PNG)"}
      </button>
      {error && <p className="text-xs text-red-700 dark:text-red-300">{error}</p>}
    </div>
  );
}
