import { type CookieOptions, createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";

type CookieToSet = { name: string; value: string; options: CookieOptions };

// Server-side code runs inside the Next.js container, so `localhost:8000`
// (the host-exposed Kong port) is unreachable.  Prefer the internal compose
// hostname via `SUPABASE_URL`, falling back to the browser URL for
// local-non-Docker development.
const serverSupabaseUrl = process.env.SUPABASE_URL ?? process.env.NEXT_PUBLIC_SUPABASE_URL!;

export function createClient() {
  const cookieStore = cookies();

  return createServerClient(serverSupabaseUrl, process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!, {
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet: CookieToSet[]) {
        try {
          for (const { name, value, options } of cookiesToSet) {
            cookieStore.set(name, value, options);
          }
        } catch {
          // Called from a Server Component; middleware will handle refresh.
        }
      },
    },
  });
}
