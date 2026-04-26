"use client";

import type { DocsTree } from "@/lib/docs";
import { slugToHref } from "@/lib/docs-client";
import Link from "next/link";
import { usePathname } from "next/navigation";

// Japanese labels for the category slugs used in front-matter.
const CATEGORY_LABELS: Record<string, string> = {
  pipelines: "パイプライン",
  statistics: "統計 & 検証",
  reference: "リファレンス",
};

type Props = {
  tree: DocsTree;
};

export function DocsSidebar({ tree }: Props) {
  const pathname = usePathname();
  const normalize = (href: string) => href.replace(/\/$/, "");
  const current = normalize(pathname ?? "");

  const renderLink = (title: string, slug: string[]) => {
    const href = slugToHref(slug);
    const active = normalize(href) === current;
    return (
      <li key={slug.join("/")}>
        <Link
          href={href}
          className={`block rounded px-2 py-1 text-sm ${
            active
              ? "bg-neutral-900 text-white dark:bg-white dark:text-neutral-900"
              : "text-neutral-700 hover:bg-neutral-100 dark:text-neutral-300 dark:hover:bg-neutral-900"
          }`}
        >
          {title}
        </Link>
      </li>
    );
  };

  return (
    <nav className="space-y-6 text-sm">
      {tree.roots.length > 0 && (
        <div>
          <ul className="space-y-0.5">{tree.roots.map((d) => renderLink(d.title, d.slug))}</ul>
        </div>
      )}
      {tree.byCategory.map(({ category, docs }) => (
        <div key={category}>
          <div className="mb-1 px-2 text-[11px] font-semibold uppercase tracking-wider text-neutral-500 dark:text-neutral-400">
            {CATEGORY_LABELS[category] ?? category}
          </div>
          <ul className="space-y-0.5">{docs.map((d) => renderLink(d.title, d.slug))}</ul>
        </div>
      ))}
    </nav>
  );
}
