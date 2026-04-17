"use client";

import dynamic from "next/dynamic";
import type { AnnotationEditorProps } from "./AnnotationEditor.client";

// Konva reads `window`/`document` on import — wrap the editor in a
// client-only dynamic import so Next.js doesn't try to render it during SSR.
const Inner = dynamic(
  () => import("./AnnotationEditor.client").then((m) => m.AnnotationEditorInner),
  {
    ssr: false,
    loading: () => (
      <p className="rounded border border-dashed border-neutral-300 p-8 text-center text-sm text-neutral-500 dark:border-neutral-700">
        エディタを読み込み中…
      </p>
    ),
  },
);

export function AnnotationEditor(props: AnnotationEditorProps) {
  return <Inner {...props} />;
}
