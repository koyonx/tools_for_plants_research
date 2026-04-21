// Hand-written Supabase types.  Keep in sync with volumes/db/init/01-schema.sql.
// In the future we can generate this via `supabase gen types typescript`.

export type Visibility = "private" | "lab" | "public";

export type PhotosynthesisType = "C3" | "C4" | "C3-C4" | "CAM" | "unknown";

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
  // PR #8 study metadata
  species: string | null;
  photosynthesis_type: PhotosynthesisType | null;
  plant_id: string | null;
  treatment: string | null;
  captured_at: string | null;
  created_at: string;
  updated_at: string;
};

export type BatchRunRow = {
  id: string;
  owner_id: string;
  label: string | null;
  criteria: Record<string, unknown>;
  pipeline_kinds: string[];
  image_ids: string[];
  analysis_ids: string[];
  status: "pending" | "running" | "done" | "partial" | "error";
  total: number;
  succeeded: number;
  failed: number;
  error: string | null;
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

export type MeasurementResult = {
  leaf_area_um2: number;
  leaf_mean_thickness_um: number;
  leaf_median_thickness_um: number;
  leaf_min_thickness_um: number;
  leaf_max_thickness_um: number;
  thickness_profile_um: number[];
  thickness_profile_x_um: number[];
  valid_columns: number;
};

export type BasicMeasurementResult = {
  scale: { um_per_px: number; bar_px_length: number; bbox_xywh: number[] };
  measurement: MeasurementResult;
  image_shape: { height_px: number; width_px: number };
};

export type CellposeCell = {
  polygon: [number, number][]; // original image pixel space
  centroid: [number, number];
  area_px: number;
};

export type CellposeResult = {
  model: string;
  cell_count: number;
  downsample_factor: number;
  mean_area_px: number;
  median_area_px: number;
  cells: CellposeCell[];
  image_shape: { height_px: number; width_px: number };
};

export type SegFormerClassCoverage = {
  class_key: string;
  pixel_count: number;
  area_px: number;
  coverage_ratio: number;
};

export type SegFormerPolygon = {
  class_key: string;
  polygon: [number, number][];
  area_px: number;
  // Inner rings (holes) — rendered with SVG fill-rule="evenodd" so any
  // class enclosed by this polygon stays visible underneath.
  holes?: [number, number][][];
};

export type SegFormerResult = {
  model_dir: string;
  classes: string[];
  coverage: SegFormerClassCoverage[];
  polygons: SegFormerPolygon[];
  downsample_factor: number;
  image_shape: { height_px: number; width_px: number };
};

export type StomatumPath = {
  centroid: [number, number];
  travel_time: number;
  travel_time_um: number | null;
  straight_line_um: number | null;
  nearest_source: [number, number];
  // Polyline traced down the FMM travel-time gradient (original-image
  // coords).  Frontend renders it instead of the straight-line link
  // when present.
  route?: [number, number][];
  // True when the trace was snapped to the Euclidean-nearest source
  // because gradient descent plateaued or hit the step budget — UI
  // dashes that final segment.
  truncated?: boolean;
};

export type WaterPathResult = {
  source_class: "xylem_vessel" | "xylem";
  sink_count: number;
  travel_time_min: number;
  travel_time_mean: number;
  travel_time_max: number;
  travel_time_p50: number;
  paths: StomatumPath[];
  heatmap_png_base64: string;
  heatmap_shape: [number, number];
  downsample_factor: number;
  resistance: Record<string, number>;
};

export type AnalysisRow = {
  id: string;
  image_id: string;
  kind: string;
  status: "pending" | "running" | "done" | "error";
  parameters: Record<string, unknown> | null;
  result: BasicMeasurementResult | Record<string, unknown> | null;
  error: string | null;
  created_at: string;
  updated_at: string;
};

export type AnnotationRow = {
  id: string;
  image_id: string;
  owner_id: string;
  class: string;
  polygon: number[][]; // [[x, y], ...] in image-pixel coordinates
  note: string | null;
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
