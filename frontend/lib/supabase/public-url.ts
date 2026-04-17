// `createSignedUrl()` on the server returns a URL prefixed with whatever
// Supabase URL the server client was built with — under Docker Compose that
// is `http://supabase-kong:8000`, which the browser cannot resolve.  Swap
// the host for the browser-reachable public URL before handing the link to
// React so <img src> actually works.

const internalSupabaseUrl = process.env.SUPABASE_URL ?? process.env.NEXT_PUBLIC_SUPABASE_URL;
const publicSupabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;

export function toPublicSupabaseUrl(url: string): string {
  if (!internalSupabaseUrl || !publicSupabaseUrl) return url;
  if (internalSupabaseUrl === publicSupabaseUrl) return url;
  return url.startsWith(internalSupabaseUrl)
    ? publicSupabaseUrl + url.slice(internalSupabaseUrl.length)
    : url;
}
