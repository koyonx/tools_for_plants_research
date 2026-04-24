import { LiteratureTable } from "@/components/LiteratureTable";
import Link from "next/link";

export const dynamic = "force-dynamic";

export default async function LiteraturePage() {
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">文献範囲カタログ</h1>
        <Link href="/dashboard" className="text-sm text-neutral-500 underline">
          ← 画像一覧
        </Link>
      </div>
      <p className="text-sm text-neutral-600 dark:text-neutral-400">
        各パラメータの C3 / C4 / CAM 別の文献レンジ (min / typical / max) と出典を一覧表示します。
        画像詳細ページとガス交換セッションの <code className="font-mono">文献照合</code>{" "}
        バッジはこのカタログから範囲を引いて判定しています。範囲追加は
        <code className="font-mono">backend/app/pipeline/literature_ranges.py</code>
        に新しい <code className="font-mono">LiteratureRange</code> を追加してください。
      </p>
      <LiteratureTable />
    </div>
  );
}
