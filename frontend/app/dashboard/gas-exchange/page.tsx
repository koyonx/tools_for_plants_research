import { GasExchangeDashboard } from "@/components/GasExchangeDashboard";
import { createClient } from "@/lib/supabase/server";
import type { GasExchangeSessionRow, ImageRow } from "@/lib/supabase/types";
import Link from "next/link";

export const dynamic = "force-dynamic";

export default async function GasExchangePage() {
  const supabase = createClient();
  // Load sessions + a thin slice of images so the filter dropdowns
  // and cross-link panel can populate without a client round-trip.
  const [sessionsResp, imagesResp] = await Promise.all([
    supabase
      .from("gas_exchange_sessions")
      .select("*")
      .order("captured_at", { ascending: false })
      .order("created_at", { ascending: false })
      .limit(500),
    supabase
      .from("images")
      .select("species, photosynthesis_type, plant_id, treatment")
      .order("created_at", { ascending: false })
      .limit(1000),
  ]);

  const sessions = (sessionsResp.data ?? []) as GasExchangeSessionRow[];
  const images = (imagesResp.data ?? []) as Pick<
    ImageRow,
    "species" | "photosynthesis_type" | "plant_id" | "treatment"
  >[];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">ガス交換 (LI-COR)</h1>
        <Link href="/dashboard" className="text-sm text-neutral-500 underline">
          ← 画像一覧
        </Link>
      </div>
      <GasExchangeDashboard initialSessions={sessions} images={images} />
    </div>
  );
}
