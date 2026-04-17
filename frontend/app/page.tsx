export default function HomePage() {
  const backendUrl = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8001";

  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col gap-8 px-6 py-16">
      <header>
        <h1 className="text-3xl font-bold tracking-tight">tools_for_plants_research</h1>
        <p className="mt-2 text-sm text-neutral-500">
          植物組織画像の全自動解析ツール (infrastructure scaffold)
        </p>
      </header>

      <section className="rounded-lg border border-neutral-200 p-6 dark:border-neutral-800">
        <h2 className="text-lg font-semibold">Status</h2>
        <ul className="mt-3 space-y-1 text-sm">
          <li>
            Backend:{" "}
            <code className="rounded bg-neutral-100 px-1.5 py-0.5 dark:bg-neutral-800">
              {backendUrl}/health
            </code>
          </li>
          <li>
            Supabase Studio:{" "}
            <code className="rounded bg-neutral-100 px-1.5 py-0.5 dark:bg-neutral-800">
              http://localhost:3001
            </code>
          </li>
          <li>
            Supabase API (Kong):{" "}
            <code className="rounded bg-neutral-100 px-1.5 py-0.5 dark:bg-neutral-800">
              http://localhost:8000
            </code>
          </li>
        </ul>
      </section>

      <section className="rounded-lg border border-neutral-200 p-6 dark:border-neutral-800">
        <h2 className="text-lg font-semibold">Roadmap</h2>
        <ol className="mt-3 list-decimal space-y-1 pl-5 text-sm text-neutral-600 dark:text-neutral-400">
          <li>インフラ土台 (本PR)</li>
          <li>認証 + 画像アップロード + ビューワー</li>
          <li>スケール検出 + 葉領域抽出 + 基本計測</li>
          <li>アノテーションワークフロー</li>
          <li>組織多クラス分割 + 気孔/維管束検出</li>
          <li>最短経路 + 透水マップ</li>
          <li>精度検証 + リリース整備</li>
        </ol>
      </section>
    </main>
  );
}
