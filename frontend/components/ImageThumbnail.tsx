import { createClient } from "@/lib/supabase/server";

export async function ImageThumbnail({
  storagePath,
  alt,
}: {
  storagePath: string;
  alt: string;
}) {
  const supabase = createClient();
  const { data } = await supabase.storage.from("images").createSignedUrl(storagePath, 60 * 60);

  if (!data?.signedUrl) {
    return (
      <div className="flex aspect-video items-center justify-center bg-neutral-100 text-xs text-neutral-500 dark:bg-neutral-900">
        プレビュー不可
      </div>
    );
  }
  return (
    <img
      src={data.signedUrl}
      alt={alt}
      className="aspect-video w-full bg-neutral-50 object-cover dark:bg-neutral-900"
      loading="lazy"
    />
  );
}
