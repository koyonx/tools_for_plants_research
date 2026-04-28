"use client";

import { createClient } from "@/lib/supabase/client";
import type { Visibility } from "@/lib/supabase/types";
import { useRouter } from "next/navigation";
import { useCallback, useRef, useState } from "react";
import { errorMessage } from "@/lib/error-message";

type ImageMeta = { width: number; height: number };

async function readImageMeta(file: File): Promise<ImageMeta> {
  return await new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      resolve({ width: img.naturalWidth, height: img.naturalHeight });
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("画像を読み込めませんでした"));
    };
    img.src = url;
  });
}

function sanitiseFilename(name: string) {
  return name.replace(/[^\w.\-]+/g, "_").slice(0, 120);
}

type UploaderProps = {
  // Where to send the user after a successful upload.  "detail" (default)
  // jumps to the analysis page; "annotate" jumps straight to the
  // polygon editor — used when the user came from the annotation flow
  // and just wants to keep labelling.
  afterUpload?: "detail" | "annotate";
};

export function Uploader({ afterUpload = "detail" }: UploaderProps = {}) {
  const router = useRouter();
  const supabase = createClient();
  const inputRef = useRef<HTMLInputElement>(null);

  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [visibility, setVisibility] = useState<Visibility>("private");
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

  const pickFile = useCallback(
    (f: File | null) => {
      setError(null);
      if (!f) return;
      if (!f.type.startsWith("image/")) {
        setError("画像ファイルを選んでください");
        return;
      }
      setFile(f);
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      setPreviewUrl(URL.createObjectURL(f));
    },
    [previewUrl],
  );

  const onDrop = (e: React.DragEvent<HTMLButtonElement>) => {
    e.preventDefault();
    setDragging(false);
    const f = e.dataTransfer.files?.[0];
    if (f) pickFile(f);
  };

  const submit = async () => {
    if (!file) return;
    setUploading(true);
    setError(null);
    try {
      const { data: userData, error: userErr } = await supabase.auth.getUser();
      if (userErr || !userData.user) throw new Error("セッションが切れました");
      const userId = userData.user.id;

      const meta = await readImageMeta(file).catch(() => null);
      const imageId = crypto.randomUUID();
      const cleanName = sanitiseFilename(file.name);
      const storagePath = `${userId}/${imageId}/${cleanName}`;

      const { error: upErr } = await supabase.storage.from("images").upload(storagePath, file, {
        cacheControl: "3600",
        contentType: file.type,
        upsert: false,
      });
      if (upErr) throw upErr;

      const { error: insErr } = await supabase.from("images").insert({
        id: imageId,
        owner_id: userId,
        visibility,
        source: "manual_upload",
        storage_path: storagePath,
        original_filename: file.name,
        content_type: file.type,
        width_px: meta?.width ?? null,
        height_px: meta?.height ?? null,
      });
      if (insErr) {
        // try to clean up the orphan object so the bucket doesn't leak
        await supabase.storage.from("images").remove([storagePath]);
        throw insErr;
      }

      // The annotate page hard-blocks images without recorded
      // dimensions, so if `readImageMeta` failed (corrupt JPEG, browser
      // refused to decode, …) we silently fall back to the detail page
      // instead of dropping the user on a "サイズ未取得" error screen.
      const goToAnnotate = afterUpload === "annotate" && meta != null;
      router.push(
        goToAnnotate
          ? `/dashboard/images/${imageId}/annotate`
          : `/dashboard/images/${imageId}`,
      );
    } catch (e) {
      setError(errorMessage(e));
      setUploading(false);
    }
  };

  return (
    <div className="space-y-4">
      <button
        type="button"
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        className={`flex w-full cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed p-10 text-sm ${
          dragging
            ? "border-neutral-900 bg-neutral-50 dark:border-white dark:bg-neutral-900"
            : "border-neutral-300 dark:border-neutral-700"
        }`}
      >
        {previewUrl ? (
          <img src={previewUrl} alt="preview" className="max-h-80 rounded object-contain" />
        ) : (
          <>
            <span className="font-medium">画像をドラッグ＆ドロップ</span>
            <span className="mt-1 text-neutral-500">またはクリックしてファイルを選択</span>
          </>
        )}
        <input
          ref={inputRef}
          type="file"
          accept="image/*"
          className="hidden"
          onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
        />
      </button>

      {file && (
        <div className="flex items-center justify-between text-sm">
          <span className="truncate">
            {file.name} ({Math.round(file.size / 1024)} KB)
          </span>
          <button
            type="button"
            onClick={() => {
              setFile(null);
              if (previewUrl) URL.revokeObjectURL(previewUrl);
              setPreviewUrl(null);
            }}
            className="text-neutral-500 underline"
          >
            変更
          </button>
        </div>
      )}

      <fieldset className="space-y-2 text-sm">
        <legend className="font-medium">公開範囲</legend>
        {(["private", "lab", "public"] as Visibility[]).map((v) => (
          <label key={v} className="flex items-center gap-2">
            <input
              type="radio"
              name="visibility"
              checked={visibility === v}
              onChange={() => setVisibility(v)}
            />
            <span className="font-mono">{v}</span>
            <span className="text-neutral-500">
              {v === "private" && "— 自分のみ"}
              {v === "lab" && "— ログインユーザー全員"}
              {v === "public" && "— 誰でも（将来の外部公開対象）"}
            </span>
          </label>
        ))}
      </fieldset>

      {error && (
        <p className="rounded bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950 dark:text-red-200">
          {error}
        </p>
      )}

      <button
        type="button"
        onClick={submit}
        disabled={!file || uploading}
        className="rounded bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50 dark:bg-white dark:text-neutral-900"
      >
        {uploading ? "アップロード中…" : "アップロード"}
      </button>
    </div>
  );
}
