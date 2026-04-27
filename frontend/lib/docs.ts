import { promises as fs, type Dirent } from "node:fs";
import path from "node:path";
import matter from "gray-matter";

const DOCS_ROOT = path.join(process.cwd(), "content", "docs");

export type DocMeta = {
  slug: string[];
  title: string;
  description?: string;
  category?: string;
  order: number;
};

export type LoadedDoc = {
  meta: DocMeta;
  body: string;
};

// Walk DOCS_ROOT and return every .md file.  Server-side only — this
// function touches the filesystem, so callers must be server components
// or route handlers.  Resilient to two failure modes the round-3 audit
// flagged:
//   - DOCS_ROOT does not exist (returns empty list rather than letting
//     the layout-level server component crash with ENOENT).
//   - A .md entry resolves to a broken symlink (skipped silently;
//     `getDocsTree` keeps rendering the docs that ARE readable).
async function walk(dir: string, base: string[] = []): Promise<string[][]> {
  let entries: Dirent[];
  try {
    entries = (await fs.readdir(dir, { withFileTypes: true })) as Dirent[];
  } catch {
    return [];
  }
  const out: string[][] = [];
  for (const entry of entries) {
    const next = [...base, entry.name];
    if (entry.isDirectory()) {
      out.push(...(await walk(path.join(dir, entry.name), next)));
    } else if (entry.isFile() && entry.name.endsWith(".md")) {
      // drop the .md extension from the leaf segment
      next[next.length - 1] = entry.name.replace(/\.md$/, "");
      out.push(next);
    }
  }
  return out;
}

function parseMetaFromFrontmatter(slug: string[], data: Record<string, unknown>): DocMeta {
  const title = typeof data.title === "string" ? data.title : slug.join("/");
  const description = typeof data.description === "string" ? data.description : undefined;
  const category = typeof data.category === "string" ? data.category : undefined;
  const order = typeof data.order === "number" ? data.order : 1000;
  return { slug, title, description, category, order };
}

export async function listDocs(): Promise<DocMeta[]> {
  const slugs = await walk(DOCS_ROOT);
  // Resilient per-file read: a broken symlink or a permission error on
  // a single .md file no longer takes down the whole sidebar — the
  // unreadable file is dropped from the index instead.
  const metaResults = await Promise.all(
    slugs.map(async (slug): Promise<DocMeta | null> => {
      try {
        const raw = await fs.readFile(path.join(DOCS_ROOT, ...slugFile(slug)), "utf8");
        const { data } = matter(raw);
        return parseMetaFromFrontmatter(slug, data);
      } catch {
        return null;
      }
    }),
  );
  const metas = metaResults.filter((m): m is DocMeta => m !== null);
  metas.sort((a, b) => {
    if (a.order !== b.order) return a.order - b.order;
    return a.title.localeCompare(b.title);
  });
  return metas;
}

export async function loadDoc(slug: string[]): Promise<LoadedDoc | null> {
  // Guard against path traversal — each segment must be a safe filename.
  for (const seg of slug) {
    if (!/^[a-z0-9][a-z0-9._-]*$/i.test(seg)) return null;
  }
  const filePath = path.join(DOCS_ROOT, ...slugFile(slug));

  // Round-1 BLOCKER: the slug regex blocks `..`, but a stray symlink
  // inside `content/docs/` could still escape DOCS_ROOT.  Resolve
  // through realpath and refuse to read anything that lands outside.
  let resolvedPath: string;
  let resolvedRoot: string;
  try {
    resolvedPath = await fs.realpath(filePath);
    resolvedRoot = await fs.realpath(DOCS_ROOT);
  } catch {
    return null;
  }
  const rel = path.relative(resolvedRoot, resolvedPath);
  if (rel === "" || rel.startsWith("..") || path.isAbsolute(rel)) return null;

  let raw: string;
  try {
    raw = await fs.readFile(resolvedPath, "utf8");
  } catch {
    return null;
  }
  const { data, content } = matter(raw);
  return {
    meta: parseMetaFromFrontmatter(slug, data),
    body: content,
  };
}

function slugFile(slug: string[]): string[] {
  // Reattach the .md extension to the last segment
  const segments = [...slug];
  segments[segments.length - 1] = `${segments[segments.length - 1]}.md`;
  return segments;
}

export { slugToHref } from "./docs-client";

export type DocsTree = {
  roots: DocMeta[];
  byCategory: { category: string; docs: DocMeta[] }[];
};

export async function getDocsTree(): Promise<DocsTree> {
  const docs = await listDocs();
  const roots = docs.filter((d) => !d.category);
  const catMap = new Map<string, DocMeta[]>();
  for (const d of docs) {
    if (!d.category) continue;
    const bucket = catMap.get(d.category) ?? [];
    bucket.push(d);
    catMap.set(d.category, bucket);
  }
  // Deterministic category order: overview-like roots on top, then
  // custom categories in insertion (i.e. order-sorted) order.
  const byCategory: DocsTree["byCategory"] = [];
  for (const [category, list] of catMap) {
    list.sort((a, b) => a.order - b.order || a.title.localeCompare(b.title));
    byCategory.push({ category, docs: list });
  }
  return { roots, byCategory };
}
