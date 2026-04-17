import { createClient } from "@/lib/supabase/client";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8001";

/**
 * Fetch a backend URL with the current user's Supabase access token and
 * trigger a browser download.  Returns a { ok, error } result so callers
 * can surface failure messages inline instead of getting an exception.
 */
export async function downloadAuthed(
  path: string,
  suggestedFilename: string,
): Promise<{ ok: true } | { ok: false; error: string }> {
  const supabase = createClient();
  const { data: sess } = await supabase.auth.getSession();
  if (!sess.session) return { ok: false, error: "セッションが切れました" };

  const resp = await fetch(`${BACKEND_URL}${path}`, {
    headers: { Authorization: `Bearer ${sess.session.access_token}` },
  });
  if (!resp.ok) {
    const detail = await resp.text().catch(() => "");
    return { ok: false, error: `${resp.status}: ${detail.slice(0, 200)}` };
  }

  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = suggestedFilename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  return { ok: true };
}
