import { createClient } from "@/lib/supabase/server";
import { type NextRequest, NextResponse } from "next/server";

export async function POST(request: NextRequest) {
  const supabase = createClient();
  await supabase.auth.signOut();
  // 303 so the browser follows with GET; default is 307 which would POST
  // to /login and break the page (that route only serves GET).
  return NextResponse.redirect(new URL("/login", request.url), { status: 303 });
}
