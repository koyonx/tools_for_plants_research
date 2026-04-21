import { CompareDashboard } from "@/components/CompareDashboard";
import { createClient } from "@/lib/supabase/server";
import type { ImageRow } from "@/lib/supabase/types";
import Link from "next/link";

export const dynamic = "force-dynamic";

export default async function ComparePage() {
  const supabase = createClient();
  const { data: images } = await supabase
    .from("images")
    .select("species, photosynthesis_type, plant_id, treatment")
    .order("created_at", { ascending: false })
    .limit(1000);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">比較ダッシュボード</h1>
        <Link href="/dashboard" className="text-sm text-neutral-500 underline">
          ← 画像一覧
        </Link>
      </div>
      <CompareDashboard
        images={
          (images ?? []) as Pick<
            ImageRow,
            "species" | "photosynthesis_type" | "plant_id" | "treatment"
          >[]
        }
      />
    </div>
  );
}
