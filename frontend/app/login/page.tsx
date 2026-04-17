import { createClient } from "@/lib/supabase/server";
import { redirect } from "next/navigation";

type SearchParams = { next?: string; sent?: string; error?: string };

async function sendMagicLink(formData: FormData) {
  "use server";
  const email = String(formData.get("email") ?? "").trim();
  const next = String(formData.get("next") ?? "/dashboard");
  if (!email) redirect("/login?error=email-required");

  const supabase = createClient();
  const siteUrl = process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000";
  const { error } = await supabase.auth.signInWithOtp({
    email,
    options: {
      emailRedirectTo: `${siteUrl}/auth/callback?next=${encodeURIComponent(next)}`,
    },
  });

  if (error) {
    redirect(`/login?error=${encodeURIComponent(error.message)}`);
  }
  redirect(`/login?sent=1&next=${encodeURIComponent(next)}`);
}

export default function LoginPage({ searchParams }: { searchParams: SearchParams }) {
  const next = searchParams.next ?? "/dashboard";
  const sent = searchParams.sent === "1";
  const error = searchParams.error;

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-6">
      <div className="rounded-lg border border-neutral-200 p-6 dark:border-neutral-800">
        <h1 className="text-xl font-semibold">サインイン</h1>
        <p className="mt-1 text-sm text-neutral-500">
          メールアドレスにマジックリンクを送信します。
        </p>

        <form action={sendMagicLink} className="mt-6 space-y-3">
          <input type="hidden" name="next" value={next} />
          <label className="block text-sm font-medium" htmlFor="email">
            メールアドレス
          </label>
          <input
            id="email"
            name="email"
            type="email"
            required
            autoComplete="email"
            placeholder="you@example.com"
            className="w-full rounded border border-neutral-300 bg-transparent px-3 py-2 text-sm outline-none focus:border-neutral-500 dark:border-neutral-700"
          />
          <button
            type="submit"
            className="w-full rounded bg-neutral-900 px-3 py-2 text-sm font-medium text-white hover:bg-neutral-800 dark:bg-white dark:text-neutral-900 dark:hover:bg-neutral-200"
          >
            マジックリンクを送る
          </button>
        </form>

        {sent && (
          <p className="mt-4 rounded bg-green-50 p-3 text-sm text-green-800 dark:bg-green-950 dark:text-green-200">
            メールを送信しました。受信箱を確認してください（開発環境では Supabase コンテナのログに
            magic link が出力されます）。
          </p>
        )}
        {error && (
          <p className="mt-4 rounded bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950 dark:text-red-200">
            エラー: {error}
          </p>
        )}
      </div>
    </main>
  );
}
