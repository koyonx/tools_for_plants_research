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

// PR #9 — comparison dashboard types
export type CompareMetricDef = {
  key: string;
  label: string;
  unit: string;
  analysis_kind: string;
  path: string[];
};

export type CompareGroupStats = {
  n: number;
  // Summary fields are `null` when n=0 (instead of NaN) so the JSON
  // response stays RFC-8259 compliant.  Use `fmt()` on the frontend.
  mean: number | null;
  sd: number | null;
  median: number | null;
  q25: number | null;
  q75: number | null;
  min: number | null;
  max: number | null;
  image_ids: string[];
  values: number[];
  /** True when the backend truncated raw values for payload-size reasons. */
  values_truncated?: boolean;
};

export type CompareMetricResult = {
  metric: CompareMetricDef;
  group_a: CompareGroupStats;
  group_b: CompareGroupStats;
  tests: {
    welch_t_statistic: number | null;
    welch_p_value: number | null;
    mann_whitney_u: number | null;
    mann_whitney_p_value: number | null;
  };
  effect_size: {
    cohens_d: number | null;
    hedges_g: number | null;
    hedges_g_ci_low: number | null;
    hedges_g_ci_high: number | null;
  };
  notes: string[];
};

export type CompareResponse = {
  group_a: { filter: Record<string, string>; image_count: number };
  group_b: { filter: Record<string, string>; image_count: number };
  metrics: CompareMetricResult[];
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

// PR #10 — CO2 diffusion morphometrics.  All numeric fields may be
// null when the pipeline couldn't compute them (missing prerequisite,
// empty mesophyll mask, low-contrast chloroplast detection, etc).
// JSON-safe: the backend replaces NaN/Inf with null before returning.
export type Co2MesophyllStats = {
  area_px: number;
  area_um2: number | null;
  thickness_mean_um: number | null;
  thickness_median_um: number | null;
  section_length_um: number | null;
  section_length_px: number;
};

export type Co2CellAggregateStats = {
  count: number;
  perimeter_total_um: number | null;
  perimeter_total_px: number;
  area_total_um2: number | null;
  area_total_px: number;
  mean_perimeter_um: number | null;
  mean_area_um2: number | null;
};

export type Co2ChloroplastStats = {
  count: number;
  total_area_px: number;
  total_area_um2: number | null;
  mean_area_um2: number | null;
  total_perimeter_um: number | null;
  coverage_of_mesophyll_cells: number | null;
  detection_method: string;
  a_channel_contrast: number;
};

export type Co2CellWallStats = {
  t_cw_mean_um: number | null;
  t_cw_median_um: number | null;
  t_cw_p95_um: number | null;
  t_cw_mean_px: number;
  t_cw_median_px: number;
  t_cw_p95_px: number;
  gap_pixel_count: number;
};

export type Co2MorphometricsResult = {
  source_class: string[];
  downsample_factor: number;
  um_per_px: number | null;
  image_shape: { height_px: number; width_px: number };
  mesophyll: Co2MesophyllStats;
  mesophyll_cells: Co2CellAggregateStats;
  chloroplasts: Co2ChloroplastStats;
  cell_wall: Co2CellWallStats;
  // Top-level dimensionless scalars — Evans & von Caemmerer 2-D proxies.
  s_mes_s: number | null;
  s_c_s: number | null;
  f_ias: number | null;
  chloroplast_overlay_png_base64: string;
  notes: string[];
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
