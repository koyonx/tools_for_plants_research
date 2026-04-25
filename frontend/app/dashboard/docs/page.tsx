import { MarkdownDoc } from "@/components/docs/MarkdownDoc";
import { loadDoc } from "@/lib/docs";
import { notFound } from "next/navigation";

export default async function DocsIndexPage() {
  const doc = await loadDoc(["index"]);
  if (!doc) notFound();
  return (
    <article>
      <header className="mb-6 border-b border-neutral-200 pb-4 dark:border-neutral-800">
        <h1 className="text-3xl font-bold">{doc.meta.title}</h1>
        {doc.meta.description ? (
          <p className="mt-2 text-neutral-600 dark:text-neutral-400">{doc.meta.description}</p>
        ) : null}
      </header>
      <MarkdownDoc source={doc.body} />
    </article>
  );
}
