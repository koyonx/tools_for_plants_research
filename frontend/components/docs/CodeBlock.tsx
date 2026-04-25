"use client";

import { useEffect, useState } from "react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark, oneLight } from "react-syntax-highlighter/dist/esm/styles/prism";

type Props = {
  language: string;
  code: string;
};

export function CodeBlock({ language, code }: Props) {
  // Match the dashboard's light/dark theme at runtime.  `prefers-color-
  // scheme` covers the initial render; the listener keeps existing code
  // blocks in sync if the user flips the OS theme without reloading.
  const [dark, setDark] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    setDark(mq.matches);
    const listener = (e: MediaQueryListEvent) => setDark(e.matches);
    mq.addEventListener("change", listener);
    return () => mq.removeEventListener("change", listener);
  }, []);

  return (
    <SyntaxHighlighter
      language={language || "text"}
      style={dark ? oneDark : oneLight}
      customStyle={{
        borderRadius: "0.375rem",
        fontSize: "0.85rem",
        padding: "0.9rem 1rem",
        margin: "0.75rem 0",
      }}
      wrapLongLines
    >
      {code.replace(/\n$/, "")}
    </SyntaxHighlighter>
  );
}
