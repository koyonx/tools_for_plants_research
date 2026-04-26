import { DocsSidebar } from "@/components/docs/DocsSidebar";
import { getDocsTree } from "@/lib/docs";
import "katex/dist/katex.min.css";
import "./docs.css";

export const dynamic = "force-dynamic";

export default async function DocsLayout({ children }: { children: React.ReactNode }) {
  const tree = await getDocsTree();
  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[220px_1fr]">
      <aside className="lg:sticky lg:top-4 lg:self-start">
        <DocsSidebar tree={tree} />
      </aside>
      <main className="min-w-0">{children}</main>
    </div>
  );
}
