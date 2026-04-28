// Shared cookie key — must match across browser/server/middleware so the
// session set on one side is visible on the other.  By default
// @supabase/ssr derives this from the URL host, but our server uses
// `supabase-kong` while the browser uses `localhost`, which would split
// the cookie under two different names.
export const SUPABASE_STORAGE_KEY = "sb-plants-auth";
