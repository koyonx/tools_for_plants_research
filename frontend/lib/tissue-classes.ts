// Single source of truth for tissue class taxonomy.  Kept in sync with
// backend/app/pipeline/classes.py.  Colours intentionally span the hue
// wheel so overlapping annotations stay distinguishable on-canvas.

export type TissueClassKey =
  | "upper_epidermis"
  | "lower_epidermis"
  | "palisade"
  | "spongy"
  | "bundle_sheath"
  | "xylem"
  | "phloem"
  | "stomata"
  | "intercellular"
  | "other";

export type TissueClass = {
  key: TissueClassKey;
  label: string;
  color: string; // hex
};

export const TISSUE_CLASSES: readonly TissueClass[] = [
  { key: "upper_epidermis", label: "上側表皮", color: "#ef4444" },
  { key: "lower_epidermis", label: "下側表皮", color: "#f97316" },
  { key: "palisade", label: "柵状葉肉", color: "#eab308" },
  { key: "spongy", label: "海綿状葉肉", color: "#22c55e" },
  { key: "bundle_sheath", label: "維管束鞘", color: "#06b6d4" },
  { key: "xylem", label: "木部", color: "#3b82f6" },
  { key: "phloem", label: "師部", color: "#8b5cf6" },
  { key: "stomata", label: "気孔", color: "#ec4899" },
  { key: "intercellular", label: "細胞間隙", color: "#14b8a6" },
  { key: "other", label: "その他", color: "#6b7280" },
];

export const TISSUE_CLASS_BY_KEY: Record<string, TissueClass> = Object.fromEntries(
  TISSUE_CLASSES.map((c) => [c.key, c]),
);
