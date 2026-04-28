// Pull a human-readable string out of arbitrary thrown values.
//
// `String(e)` produces "[object Object]" for plain objects, including
// Supabase's PostgrestError / StorageError (which carry a `.message` but
// aren't Error instances).  Walk the common shapes and fall back to JSON
// so the user at least sees the failure code instead of "[object Object]".
export function errorMessage(e: unknown): string {
  if (e instanceof Error) return e.message;
  if (typeof e === "string") return e;
  if (e && typeof e === "object") {
    const o = e as Record<string, unknown>;
    if (typeof o.message === "string") {
      const detail = typeof o.details === "string" ? ` (${o.details})` : "";
      const hint = typeof o.hint === "string" ? ` — ${o.hint}` : "";
      return `${o.message}${detail}${hint}`;
    }
    try {
      return JSON.stringify(e);
    } catch {
      // fallthrough
    }
  }
  return String(e);
}
