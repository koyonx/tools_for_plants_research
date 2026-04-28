import { createClient } from "@/lib/supabase/server";
import { type NextRequest, NextResponse } from "next/server";

// Dev-only: bypass PKCE by exchanging a `token_hash` from GoTrue's
// admin/generate_link directly for a session.  Used by `scripts/magic-link.sh`
// so local devs can sign in without a real SMTP setup.
//
// Defense in depth:
//  1. NODE_ENV must not be "production" (Next dev/standalone-without-build).
//  2. ALLOW_DEV_LOGIN must be "1".  This second flag stops a staging
//     deploy that forgets NODE_ENV from silently exposing the route.
//  3. `next` must be a same-origin path (no `//evil.com`-style relative
//     redirects, which most browsers resolve as cross-origin).
const DEV_LOGIN_ENABLED =
  process.env.NODE_ENV !== "production" && process.env.ALLOW_DEV_LOGIN === "1";

function safeNext(raw: string | null): string {
  if (!raw) return "/dashboard";
  // Reject anything that isn't a single-slash absolute path.  `//foo` is
  // a protocol-relative URL and `http://foo` is obviously off-origin.
  if (!raw.startsWith("/") || raw.startsWith("//")) return "/dashboard";
  return raw;
}

const NOT_FOUND = new NextResponse("not found", { status: 404 });

export async function GET(request: NextRequest) {
  if (!DEV_LOGIN_ENABLED) return NOT_FOUND;

  const { searchParams, origin } = request.nextUrl;
  const tokenHash = searchParams.get("token_hash");
  const next = safeNext(searchParams.get("next"));
  if (!tokenHash) {
    return NextResponse.redirect(`${origin}/login?error=missing-token_hash`, {
      headers: { "Cache-Control": "no-store" },
    });
  }

  const supabase = createClient();
  const { error } = await supabase.auth.verifyOtp({
    type: "magiclink",
    token_hash: tokenHash,
  });
  if (error) {
    // Don't reflect the raw GoTrue message back into a URL — it sometimes
    // carries the email or token fragment which would then live forever
    // in browser history.  A generic code keeps logs clean.
    return NextResponse.redirect(`${origin}/login?error=invalid-dev-token`, {
      headers: { "Cache-Control": "no-store" },
    });
  }
  return NextResponse.redirect(`${origin}${next}`, {
    headers: { "Cache-Control": "no-store" },
  });
}
