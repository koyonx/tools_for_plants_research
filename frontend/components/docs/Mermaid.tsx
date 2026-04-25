"use client";

import { useEffect, useId, useRef, useState } from "react";

type Props = {
  code: string;
};

// Global mermaid singleton: first render on the page initializes it,
// subsequent diagrams reuse the same parser instance.
let mermaidReady: Promise<typeof import("mermaid").default> | null = null;
function getMermaid() {
  if (!mermaidReady) {
    mermaidReady = import("mermaid").then((m) => {
      const isDark =
        typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches;
      m.default.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        theme: isDark ? "dark" : "default",
        // Let the diagram expand to the container rather than a fixed
        // pixel width — the docs layout handles responsive sizing.
        themeVariables: {
          fontFamily: "ui-sans-serif, system-ui, sans-serif",
        },
      });
      return m.default;
    });
  }
  return mermaidReady;
}

export function Mermaid({ code }: Props) {
  const id = useId().replace(/:/g, "_");
  const [svg, setSvg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    (async () => {
      try {
        const mermaid = await getMermaid();
        // `parse` throws on syntax errors with a readable message;
        // catch and surface it inline rather than leaving the diagram
        // blank.
        const parseResult = await mermaid.parse(code, { suppressErrors: true });
        if (parseResult === false) {
          throw new Error("mermaid: 構文解析に失敗しました");
        }
        const { svg: rendered } = await mermaid.render(`mermaid-${id}`, code);
        if (!cancelled) setSvg(rendered);
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
          setSvg(null);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [code, id]);

  if (error) {
    return (
      <div className="rounded border border-red-300 bg-red-50 p-3 text-xs text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
        <div className="font-semibold">mermaid レンダリングエラー</div>
        <pre className="mt-1 overflow-x-auto whitespace-pre-wrap">{error}</pre>
        <details className="mt-2">
          <summary className="cursor-pointer text-red-700 dark:text-red-300">
            元のコードを表示
          </summary>
          <pre className="mt-1 overflow-x-auto rounded bg-white p-2 text-[11px] text-neutral-900 dark:bg-neutral-900 dark:text-neutral-100">
            {code}
          </pre>
        </details>
      </div>
    );
  }

  if (svg) {
    return (
      <div
        ref={containerRef}
        className="my-4 overflow-x-auto rounded border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-950"
        // biome-ignore lint/security/noDangerouslySetInnerHtml: mermaid returns sanitized SVG via securityLevel: "strict"
        dangerouslySetInnerHTML={{ __html: svg }}
      />
    );
  }
  return (
    <div
      ref={containerRef}
      className="my-4 overflow-x-auto rounded border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-950"
    >
      <div className="text-xs text-neutral-500 dark:text-neutral-400">図を描画中…</div>
    </div>
  );
}
