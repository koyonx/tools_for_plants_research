import { createBrowserClient } from "@supabase/ssr";
import { SUPABASE_STORAGE_KEY } from "./storage-key";

export function createClient() {
  return createBrowserClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    { auth: { storageKey: SUPABASE_STORAGE_KEY } },
  );
}
