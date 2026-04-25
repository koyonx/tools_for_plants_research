import { MarkdownDoc } from "@/components/docs/MarkdownDoc";
import { loadDoc } from "@/lib/docs";
import { notFound } from "next/navigation";

type Props = {
  params: { slug: string[] };
};

export default async function DocsSlugPage({ params }: Props) {
  const doc = await loadDoc(params.slug);
  if (!doc) notFound();
  return (
    <article>
      <header className="mb-6 border-b border-neutral-200 pb-4 dark:border-neutral-800">
        <div className="text-xs text-neutral-500 dark:text-neutral-400">
          {params.slug.slice(0, -1).join(" / ") || "docs"}
        </div>
        <h1 className="mt-1 text-3xl font-bold">{doc.meta.title}</h1>
        {doc.meta.description ? (
          <p className="mt-2 text-neutral-600 dark:text-neutral-400">{doc.meta.description}</p>
        ) : null}
      </header>
      <MarkdownDoc source={doc.body} />
    </article>
  );
}
