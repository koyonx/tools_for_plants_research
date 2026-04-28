import Link from "next/link";

export type ImageNavRow = {
  id: string;
  original_filename: string | null;
  annotation_count: number;
};

type Props = {
  current: ImageNavRow;
  siblings: ImageNavRow[]; // ordered (newest-first matches the listing page)
};

/**
 * Inline navigator that lets the annotator jump straight from one image
 * to the next without bouncing through the listing page.  Showing the
 * neighbours' annotation counts at a glance also makes it easy to spot
 * which images still need work in a session.  Always rendered (even
 * with a single image) so the upload link stays one click away.
 */
export function AnnotationImageNav({ current, siblings }: Props) {
  // `siblings` is capped at 100 by the page query; if the user has more
  // images than that and the current one is among the older ones, it
  // won't appear in the strip.  Detect that case so we don't render a
  // misleading "1 / N" with the current slide silently missing.
  const idx = siblings.findIndex((s) => s.id === current.id);
  const currentInList = idx >= 0;
  const prev = currentInList && idx > 0 ? siblings[idx - 1] : null;
  const next = currentInList && idx < siblings.length - 1 ? siblings[idx + 1] : null;
  const total = siblings.length || 1;

  return (
    <div className="space-y-2 rounded-lg border border-neutral-200 p-2 text-xs dark:border-neutral-800">
      <div className="flex flex-wrap items-center gap-2">
        <NavButton
          href={prev ? `/dashboard/images/${prev.id}/annotate` : null}
          label={prev ? `← 前: ${prev.original_filename ?? prev.id.slice(0, 8)}` : "← 前"}
        />
        <span className="text-neutral-500">
          {currentInList ? `${idx + 1} / ${total}` : `— / ${total} (一覧外)`}
        </span>
        <NavButton
          href={next ? `/dashboard/images/${next.id}/annotate` : null}
          label={next ? `次: ${next.original_filename ?? next.id.slice(0, 8)} →` : "次 →"}
        />
        <Link
          href="/dashboard/upload?next=annotate"
          className="ml-auto rounded border border-neutral-300 px-2 py-1 hover:bg-neutral-50 dark:border-neutral-700 dark:hover:bg-neutral-900"
        >
          ＋ 画像をアップロード
        </Link>
      </div>
      {siblings.length > 1 ? (
        <div className="flex flex-wrap gap-1">
          {siblings.map((s) => {
            const isCurrent = s.id === current.id;
            const labelled = s.annotation_count > 0;
            return (
              <Link
                key={s.id}
                href={`/dashboard/images/${s.id}/annotate`}
                className={`rounded border px-2 py-0.5 text-[11px] ${
                  isCurrent
                    ? "border-amber-500 bg-amber-50 dark:border-amber-600 dark:bg-amber-950/40"
                    : labelled
                      ? "border-green-300 bg-green-50 hover:bg-green-100 dark:border-green-800 dark:bg-green-950/40"
                      : "border-neutral-300 hover:bg-neutral-50 dark:border-neutral-700 dark:hover:bg-neutral-900"
                }`}
                title={`${s.annotation_count} アノテーション`}
              >
                <span className="truncate">
                  {s.original_filename ?? s.id.slice(0, 8)}
                </span>
                {s.annotation_count > 0 && (
                  <span className="ml-1 text-neutral-500">({s.annotation_count})</span>
                )}
              </Link>
            );
          })}
        </div>
      ) : (
        <p className="text-neutral-500">
          他にアノテーションする画像がありません。右上のボタンから追加できます。
        </p>
      )}
    </div>
  );
}

function NavButton({ href, label }: { href: string | null; label: string }) {
  if (!href) {
    return (
      <span className="rounded border border-neutral-200 px-2 py-1 text-neutral-400 dark:border-neutral-800">
        {label}
      </span>
    );
  }
  return (
    <Link
      href={href}
      className="rounded border border-neutral-300 px-2 py-1 hover:bg-neutral-50 dark:border-neutral-700 dark:hover:bg-neutral-900"
    >
      {label}
    </Link>
  );
}
