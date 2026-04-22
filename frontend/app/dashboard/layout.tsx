import { createClient } from "@/lib/supabase/server";
import Link from "next/link";
import { redirect } from "next/navigation";

export default async function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const supabase = createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  return (
    <div className="min-h-screen">
      <header className="border-b border-neutral-200 dark:border-neutral-800">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-3">
          <Link href="/dashboard" className="font-semibold">
            plants-research
          </Link>
          <div className="flex items-center gap-4 text-sm">
            <Link
              href="/dashboard/compare"
              className="text-neutral-600 hover:underline dark:text-neutral-300"
            >
              比較
            </Link>
            <Link
              href="/dashboard/batches"
              className="text-neutral-600 hover:underline dark:text-neutral-300"
            >
              バッチ履歴
            </Link>
            <Link
              href="/dashboard/gas-exchange"
              className="text-neutral-600 hover:underline dark:text-neutral-300"
            >
              ガス交換
            </Link>
            <span className="text-neutral-500">{user.email}</span>
            <form action="/auth/signout" method="post">
              <button
                type="submit"
                className="rounded border border-neutral-300 px-3 py-1 hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-900"
              >
                サインアウト
              </button>
            </form>
          </div>
        </div>
      </header>
      <div className="mx-auto max-w-6xl px-6 py-8">{children}</div>
    </div>
  );
}
