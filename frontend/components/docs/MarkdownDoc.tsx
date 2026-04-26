"use client";

import React from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeAutolinkHeadings from "rehype-autolink-headings";
import rehypeKatex from "rehype-katex";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import rehypeSlug from "rehype-slug";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import { CodeBlock } from "./CodeBlock";
import { Mermaid } from "./Mermaid";

// Allow-list extensions on top of `defaultSchema`.  Round-1 BLOCKER:
// without sanitize, raw <style>/<link>/<object>/<embed>/<form>/<input>
// in a .md file would have been rendered.  With this schema:
//
//   - HTML5 inline semantics (kbd/mark/sub/sup/abbr/time/details) are
//     allowed (`details` already exists in defaultSchema; the rest are
//     either added or default-allowed).
//   - <video>/<source>/<iframe> are explicitly allowed but with a
//     pinned attribute list — runtime <iframe> URL whitelisting still
//     happens in the component override below.
//   - className is preserved on span/div/code/pre so that KaTeX (which
//     keys off `.math` / `.katex*`) and rehype-pretty-code do not lose
//     their styling hooks.
//   - `style` attribute is NOT allowed: it's a vector for
//     `background-image: url(javascript:...)` and similar tricks.
//   - <script>/<link>/<object>/<embed>/<form> are NOT in the allow-list
//     so they are dropped by sanitize before they ever reach React.
const sanitizeSchema = {
  ...defaultSchema,
  // Round-2: remark-gfm already prefixes footnote `id`/`href` with
  // `user-content-` to dodge id-collisions with hosting page chrome.
  // rehype-sanitize's default `clobberPrefix: "user-content-"` would
  // re-prefix the `id` (and the matched `clobber: ["name", "id"]`
  // attribute), producing `user-content-user-content-fn-1` on one
  // side and `user-content-fn-1` on the other.  The result: footnote
  // jumps and back-references stop resolving.  Disable the second
  // prefix here — remark-gfm's prefixing alone is enough.
  clobberPrefix: "",
  clobber: [],
  tagNames: [
    ...((defaultSchema.tagNames as string[] | undefined) ?? []),
    "details",
    "summary",
    "kbd",
    "mark",
    "video",
    "source",
    "iframe",
    "figure",
    "figcaption",
    "time",
    "abbr",
    "sub",
    "sup",
    "u",
    "s",
    "del",
  ],
  attributes: {
    ...defaultSchema.attributes,
    "*": [
      ...((defaultSchema.attributes?.["*"] as Array<string | [string, ...unknown[]]>) ?? []),
      "className",
      "id",
      "title",
    ],
    iframe: [
      "src",
      "title",
      "width",
      "height",
      "allow",
      "allowFullScreen",
      "frameBorder",
      "referrerPolicy",
      "loading",
    ],
    video: [
      "src",
      "controls",
      "preload",
      "poster",
      "width",
      "height",
      "loop",
      "muted",
      "playsInline",
    ],
    source: ["src", "type"],
    img: ["src", "alt", "title", "loading", "width", "height"],
    a: ["href", "title", "target", "rel"],
    code: ["className"],
    pre: ["className"],
    span: ["className"],
    div: ["className"],
    th: ["align", "scope", "colSpan", "rowSpan"],
    td: ["align", "colSpan", "rowSpan"],
    input: ["type", "checked", "disabled"],
    time: ["dateTime"],
  },
  protocols: {
    ...defaultSchema.protocols,
    src: ["http", "https", "data"],
  },
};

type Props = {
  source: string;
};

// Where raw <iframe> tags may point.  Anything else is dropped so a
// stray embed URL in a .md file can't navigate the user off-domain
// inside the docs frame.
const ALLOWED_IFRAME_HOST_RE =
  /^https:\/\/(?:[\w-]+\.)?(?:youtube\.com|youtube-nocookie\.com|player\.vimeo\.com)\//i;

// Image targets we auto-upgrade to <video>.  Covers the common
// formats our own `/docs-assets/*.mp4` uploads use plus .webm/.mov.
const VIDEO_EXT_RE = /\.(?:mp4|webm|mov|ogv|m4v)(?:\?|#|$)/i;

function isElement(node: unknown): node is React.ReactElement {
  return React.isValidElement(node);
}

function extractCodeFromPre(children: React.ReactNode): {
  language: string;
  code: string;
} | null {
  // <pre> normally wraps exactly one <code> element.  If the shape
  // drifts (e.g. raw <pre>asdf</pre> without <code> inside) we fall
  // back to the pass-through <pre> render.
  const child = Array.isArray(children) ? children[0] : children;
  if (!isElement(child) || child.type !== "code") return null;
  const props = child.props as { className?: string; children?: React.ReactNode };
  const className = props.className ?? "";
  const match = /language-([a-zA-Z0-9_+-]+)/.exec(className);
  const raw = String(props.children ?? "").replace(/\n$/, "");
  return { language: match?.[1] ?? "text", code: raw };
}

const components: Components = {
  // Never render raw <script>, even if rehype-raw parsed one out of a
  // .md file — treat it as a content bug rather than passing through.
  script: () => null,
  iframe: ({ src, title, ...rest }) => {
    if (typeof src !== "string" || !ALLOWED_IFRAME_HOST_RE.test(src)) return null;
    const width =
      typeof rest.width === "string" || typeof rest.width === "number" ? rest.width : "100%";
    const height =
      typeof rest.height === "string" || typeof rest.height === "number" ? rest.height : 420;
    return (
      <div className="my-4 overflow-hidden rounded border border-neutral-200 dark:border-neutral-800">
        <iframe
          src={src}
          title={title ?? "embedded media"}
          width={width}
          height={height}
          allow="accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
          allowFullScreen
          loading="lazy"
          referrerPolicy="no-referrer"
          style={{ display: "block", width: "100%" }}
        />
      </div>
    );
  },

  // Auto-upgrade image-syntax video links (`![caption](demo.mp4)`) to
  // a real <video> element.  The alt text falls back as the filename
  // for accessibility when the browser can't play the source.
  img: ({ src, alt, title }) => {
    const s = typeof src === "string" ? src : "";
    if (s && VIDEO_EXT_RE.test(s)) {
      return (
        <figure className="my-4">
          {/* biome-ignore lint/a11y/useMediaCaption: docs can embed silent demo loops with no transcript */}
          <video
            controls
            preload="metadata"
            className="w-full max-w-3xl rounded border border-neutral-200 dark:border-neutral-800"
          >
            <source src={s} />
            {alt}
          </video>
          {title ? (
            <figcaption className="mt-1 text-xs text-neutral-500 dark:text-neutral-400">
              {title}
            </figcaption>
          ) : null}
        </figure>
      );
    }
    return (
      <figure className="my-4">
        <img
          src={s}
          alt={alt ?? ""}
          title={title ?? undefined}
          loading="lazy"
          className="max-w-full rounded border border-neutral-200 dark:border-neutral-800"
        />
        {title ? (
          <figcaption className="mt-1 text-xs text-neutral-500 dark:text-neutral-400">
            {title}
          </figcaption>
        ) : null}
      </figure>
    );
  },

  pre: ({ children, ...props }) => {
    const extracted = extractCodeFromPre(children);
    if (!extracted) {
      return <pre {...props}>{children}</pre>;
    }
    if (extracted.language === "mermaid") {
      return <Mermaid code={extracted.code} />;
    }
    return <CodeBlock language={extracted.language} code={extracted.code} />;
  },

  // Inline code — block code paths through `pre` above.
  code: ({ children, className }) => (
    <code
      className={`rounded bg-neutral-100 px-1 py-0.5 font-mono text-[0.9em] dark:bg-neutral-800 ${
        className ?? ""
      }`}
    >
      {children}
    </code>
  ),

  table: ({ children }) => (
    <div className="my-4 overflow-x-auto rounded border border-neutral-200 dark:border-neutral-800">
      <table className="w-full border-collapse text-left text-sm">{children}</table>
    </div>
  ),
  thead: ({ children }) => (
    <thead className="bg-neutral-50 text-xs uppercase tracking-wide text-neutral-600 dark:bg-neutral-900 dark:text-neutral-400">
      {children}
    </thead>
  ),
  th: ({ children, style }) => (
    <th
      className="border-b border-neutral-200 px-3 py-2 font-semibold dark:border-neutral-800"
      style={style}
    >
      {children}
    </th>
  ),
  td: ({ children, style }) => (
    <td
      className="border-b border-neutral-100 px-3 py-2 align-top dark:border-neutral-900"
      style={style}
    >
      {children}
    </td>
  ),

  a: ({ href, children, ...rest }) => {
    const s = typeof href === "string" ? href : "";
    const isExternal = /^https?:\/\//i.test(s);
    return (
      <a
        {...rest}
        href={s}
        target={isExternal ? "_blank" : undefined}
        rel={isExternal ? "noopener noreferrer" : undefined}
        className="text-sky-700 underline underline-offset-2 hover:text-sky-900 dark:text-sky-300 dark:hover:text-sky-100"
      >
        {children}
      </a>
    );
  },

  blockquote: ({ children }) => (
    <blockquote className="my-4 rounded border-l-4 border-sky-300 bg-sky-50 px-4 py-2 text-neutral-700 dark:border-sky-700 dark:bg-sky-950/40 dark:text-neutral-300">
      {children}
    </blockquote>
  ),

  hr: () => <hr className="my-6 border-neutral-200 dark:border-neutral-800" />,

  h1: ({ children, id }) => (
    <h1 id={id} className="mt-8 mb-3 text-2xl font-bold">
      {children}
    </h1>
  ),
  h2: ({ children, id }) => (
    <h2
      id={id}
      className="mt-8 mb-3 border-b border-neutral-200 pb-1 text-xl font-bold dark:border-neutral-800"
    >
      {children}
    </h2>
  ),
  h3: ({ children, id }) => (
    <h3 id={id} className="mt-6 mb-2 text-lg font-semibold">
      {children}
    </h3>
  ),
  h4: ({ children, id }) => (
    <h4 id={id} className="mt-4 mb-2 text-base font-semibold">
      {children}
    </h4>
  ),

  ul: ({ children }) => <ul className="my-3 list-disc space-y-1 pl-6">{children}</ul>,
  ol: ({ children }) => <ol className="my-3 list-decimal space-y-1 pl-6">{children}</ol>,
  li: ({ children, className }) => (
    <li className={className?.includes("task-list-item") ? "list-none -ml-6" : ""}>{children}</li>
  ),

  p: ({ children }) => <p className="my-3 leading-7">{children}</p>,
};

export function MarkdownDoc({ source }: Props) {
  return (
    <div className="docs-prose max-w-none text-[15px] text-neutral-900 dark:text-neutral-100">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[
          // Order matters:
          //   raw  — turn raw HTML strings into HAST nodes
          //   sanitize — drop everything not in the allow-list (must run
          //     BEFORE katex so katex's own classes survive untouched)
          //   katex — replace `<span class="math">$...$</span>` with the
          //     rendered SVG/HTML output
          //   slug + autolink — anchor each heading
          rehypeRaw,
          [rehypeSanitize, sanitizeSchema],
          rehypeKatex,
          rehypeSlug,
          [
            rehypeAutolinkHeadings,
            {
              behavior: "wrap",
              properties: {
                className: "no-underline hover:underline",
              },
            },
          ],
        ]}
        components={components}
      >
        {source}
      </ReactMarkdown>
    </div>
  );
}
