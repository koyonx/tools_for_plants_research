// Hand-written Supabase types.  Keep in sync with volumes/db/init/01-schema.sql.
// In the future we can generate this via `supabase gen types typescript`.

export type Visibility = "private" | "lab" | "public";

export type ImageRow = {
  id: string;
  owner_id: string;
  visibility: Visibility;
  source: string;
  storage_path: string;
  original_filename: string | null;
  content_type: string | null;
  width_px: number | null;
  height_px: number | null;
  scale_um_per_px: number | null;
  created_at: string;
  updated_at: string;
};

export type ProfileRow = {
  id: string;
  display_name: string | null;
  role: "admin" | "member";
  created_at: string;
  updated_at: string;
};

export type Database = {
  public: {
    Tables: {
      images: {
        Row: ImageRow;
        Insert: Omit<ImageRow, "id" | "created_at" | "updated_at"> & {
          id?: string;
          created_at?: string;
          updated_at?: string;
        };
        Update: Partial<ImageRow>;
      };
      profiles: {
        Row: ProfileRow;
        Insert: Omit<ProfileRow, "created_at" | "updated_at"> & {
          created_at?: string;
          updated_at?: string;
        };
        Update: Partial<ProfileRow>;
      };
    };
    Views: Record<string, never>;
    Functions: Record<string, never>;
    Enums: { visibility: Visibility };
  };
};
