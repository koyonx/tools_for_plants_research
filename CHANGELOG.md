# Changelog

All notable changes to **tools_for_plants_research**.  Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project
hasn't tagged a release yet, so everything below is on `main`.

## [Unreleased]

### PR #10 — CO2 diffusion morphometrics (current)
- `backend/app/pipeline/morphometrics_co2.py`: classical-CV computation
  of the Evans & von Caemmerer / Tosens mesophyll-conductance inputs
  from existing SegFormer tissue polygons + Cellpose cell polygons.
  Outputs S_mes/S (Σ cell perimeter / leaf section length), S_c/S
  (Σ chloroplast perimeter / leaf section length), f_ias (1 − Σ cell
  area / mesophyll area), and a T_cw proxy from the distance transform
  of the intercellular gap region.  Chloroplast detection is a
  per-cell LAB a* Otsu with a contrast guard — drops cleanly into a
  learned detector later without changing the result schema.
- `backend/app/api/co2_morphometrics.py`: `POST /images/{id}/analyze/
  co2-morphometrics` + `GET /analyze/co2-morphometrics/status` probe.
  Requires `segformer_tissue` AND `cellpose_cells` completed; runs the
  computation in a `BackgroundTask` + `asyncio.to_thread`.
- `batches.py`: `co2_morphometrics` added to `SUPPORTED_KINDS`,
  `PipelineKind`, and `_PIPELINE_EXEC_ORDER` (last, since it needs
  both upstream pipelines).  Batch dispatcher re-uses the same
  prerequisite lookup as the per-image endpoint.
- `compare.py`: 8 new metric keys (`co2_s_mes_s`, `co2_s_c_s`,
  `co2_f_ias`, `co2_t_cw_median_um`, `co2_t_cw_p95_um`,
  `co2_chloroplast_count`, `co2_chloroplast_coverage`,
  `co2_mesophyll_thickness_median_um`) auto-surface in the dashboard.
- `Co2MorphometricsPanel.tsx` mounted on the image detail page below
  the WaterPath panel.  Dual prereq probe, result table, overlay PNG
  showing the detected chloroplasts.
- `ImageBatchPicker.tsx` gets the new pipeline checkbox.
- 9 pytest cases for morphometrics_co2: empty mesophyll, known
  perimeter-over-length ratio, f_ias = 0 / > 0, excluded cells,
  um_per_px scale invariance, chloroplast detection, low-contrast
  skip, T_cw proxy sanity, strict-JSON round-trip.

### PR #9 — statistical comparison dashboard
- `backend/app/pipeline/stats.py`: Welch's t (scipy), Mann-Whitney U,
  Cohen's d, Hedges' g (with small-sample bias correction), and a
  percentile bootstrap 95% CI for Hedges' g.  6 pytest cases cover
  sign, small-group fallback, zero-variance, identical distributions,
  NaN/inf handling, and correction bounds.
- `backend/app/api/compare.py`: `/compare/metrics` catalog (9 scalars
  across basic_measurement / cellpose_cells / water_path), `POST
  /compare` that resolves two image groups via RLS-aware filters,
  pulls each metric's analyses row in one batched PostgREST `in.(...)`
  call per kind, computes tests + effect sizes, and returns raw
  values so the frontend can draw boxplots.
- `SupabaseAuthedClient.list_analyses` added for batched filter reads.
- `/dashboard/compare` page with two filter builders (Group A / B),
  metric multi-select (pre-populated with basic-measurement keys),
  results table (n, mean±SD, Welch p, MW p, Hedges g + CI per metric),
  and per-metric SVG boxplots with deterministic jitter so repaints
  don't wiggle.
- Dashboard header gains `比較` and `バッチ履歴` quick links.

### PR #8 — study metadata + batch analysis
- `images` gains `species` / `photosynthesis_type` (C3/C4/CAM/unknown) /
  `plant_id` / `treatment` / `captured_at` columns via idempotent ALTER
  in the post-services bootstrap.
- New `batch_runs` table with ownership RLS; tracks pipeline_kinds,
  image_ids, analysis_ids, progress counters, terminal status.
- `POST /batches` fans an image×pipeline matrix over BackgroundTasks
  (CPU-bound inferences hand off to `asyncio.to_thread`).
  Dependency-aware: `water_path` resolves against the latest `done`
  `segformer_tissue` at dispatch time.
- Dashboard becomes a client-rendered picker: species / C3-C4 /
  plant_id / treatment filters, per-row checkbox, pipeline selector,
  batch-kickoff, batch history list, batch detail with progress poll.
- Inline metadata editor on image detail page (saves on blur).
- `BatchRunRow` and `PhotosynthesisType` in `lib/supabase/types.ts`.
- Groundwork for future auto-report (Phase B) and comparison
  dashboard (Phase C) consumes the same `batch_runs` + `analyses`
  structure — no refactor needed when those PRs land.

### PR #7 — validation + release polish
- `scripts/validate_against_xlsx.py` to compare basic-measurement output
  against the legacy `measure_results.xlsx` ground truth.  Supports three
  auth modes (`--access-token` for magic-link users, `--user-email` +
  password, `--service-role` for unattended runs), `--metric-map` JSON
  override, unicode-aware `_parse_value`, and strict duplicate-filename
  detection.
- `Makefile` with 30+ operator targets (`make help` for the full list),
  auto-sources `.env` in `make validate`, honours `PYTHON` override
  (defaults to `python3`), has a `make stop` non-destructive pause,
  and `make smoke` polls `/health` instead of sleeping 5 s.
- `openpyxl` added to the backend `dev` extra so
  `pip install -e 'backend[dev]'` prepares the host for validation.
- Top-level README rewritten as an end-to-end walkthrough, with GNU
  make 4+ noted as a prereq and the validation section covering both
  auth modes and the host-side `pip install` step.
- This `CHANGELOG.md`.

### PR #6 — water-transport analysis
- New tissue class `xylem_vessel` (導管) added across frontend + backend
  + DB CHECK constraint.  Falls back to `xylem` when not annotated.
- `pipeline/water_path.py`: cost-field rasteriser + `scikit-fmm`
  Fast-Marching solve + magma-style heatmap PNG.
- `POST /images/{id}/analyze/water-path` endpoint (requires a completed
  `segformer_tissue` analysis on the same image).
- `WaterPathPanel.tsx` — kickoff/poll/heatmap-overlay UI with live
  SegFormer-availability probe.
- Hardening from review rounds: scale-stable travel times via
  `dx=inv_factor`, gradient-descent route polylines (with truncated-snap
  flag), polygon-hole subtraction in the rasteriser, leaf-mask alpha
  on the heatmap, sanitised user-supplied resistance overrides.

### PR #5c — SegFormer tissue segmentation
- `notebooks/segformer_train.ipynb` fine-tunes `nvidia/mit-b0` on the
  PR #5a export zip, saves to `models/segformer/`.
- `pipeline/segformer_infer.py`: lazy-singleton model load, 1024 px
  down-sample, multi-class polygon extraction with `RETR_CCOMP` so
  hole hierarchy is preserved.
- `POST /images/{id}/analyze/segformer` + `GET /analyze/segformer/status`
  probe (requires `config.json` + `preprocessor_config.json` + weights).
- `SegFormerPanel.tsx` with class-coverage table and SVG polygon
  overlay (uses `fill-rule="evenodd"` for holes).
- `transformers` + `safetensors` added to the `ml` extra; `HF_HOME`
  cached on a `plants-hf-cache` Docker volume.

### PR #5b — Cellpose cell detection
- Cellpose 3 cyto3 generalist model wired into the analysis pipeline.
- `pipeline/cellpose_infer.py`: lazy singleton, down-sample to 1024 px,
  per-cell polygons + centroid + area.
- `POST /images/{id}/analyze/cellpose` runs in a `BackgroundTask` +
  `asyncio.to_thread` so the FastAPI event loop stays responsive.
- `CellposePanel.tsx` with terminal-gated polling that survives
  transient backend hiccups; per-cell SVG polygon overlay.
- CPU-only torch wheel + cellpose added to the `ml` extra; weights
  cached on a `plants-ml-cache` Docker volume.

### PR #5a — annotation rasteriser + training-data export
- `pipeline/rasterize.py`: polygon → uint8 semantic PNG with
  last-write-wins on overlaps and 1-based class indices.
- `GET /images/{id}/mask.png` + `GET /training/export.zip` (bundles
  every visible image + mask + classes.json + index.json).
- `MaskDownloadButton.tsx` and `TrainingExportButton.tsx` in the UI.
- 8 synthetic-image pytest cases for the rasteriser.

### PR #4 — browser-native annotation editor
- `annotations` table with idempotent migration in
  `02-after-services.sql.tmpl`, RLS gated to authenticated users,
  CHECK constraints on `class` + a custom `is_valid_polygon(jsonb)`
  function.
- Shared tissue class taxonomy (`frontend/lib/tissue-classes.ts` +
  `backend/app/pipeline/classes.py`).
- Konva-based pan/zoom/polygon editor at
  `/dashboard/images/[id]/annotate`; Space-to-pan, container-scoped
  keyboard shortcuts, init-once viewport fit.
- Deletion confirmation, vertex clamping to image bounds.

### PR #3 — basic measurements (classical CV)
- `pipeline/scale.py` — bottom-right ROI Otsu + morphology to find
  the scale bar; converts to µm/px.
- `pipeline/segment.py` — HSV saturation + Otsu + morphology for
  leaf mask.
- `pipeline/measure.py` — per-column thickness profile (≤512 sample
  points), summary stats computed on the full profile.
- `POST /images/{id}/analyze` + CSV export.  Frontend `AnalyzePanel`
  with thickness chart + CSV download.

### PR #2 — auth + image upload + viewer
- Magic-link login via Supabase GoTrue, `middleware.ts` for session
  refresh + dashboard guard.
- `images` + `profiles` tables with `visibility` (private/lab/public)
  and end-to-end RLS (DB rows + Storage objects).
- Drag-and-drop uploader, signed-URL thumbnails, `react-zoom-pan-pinch`
  detail view.

### PR #1 — infrastructure foundation
- Docker Compose stack: backend (FastAPI), frontend (Next.js 14),
  Supabase self-host (db / auth / rest / storage / kong / studio /
  meta), one-shot supabase-bootstrap for post-init SQL.
- `volumes/db/init/00-init.sql` creates roles + auth helper functions
  (`auth.uid()` / `auth.role()` / `auth.email()`) before policies that
  reference them.
- GitHub Actions CI (lint + typecheck + test on backend & frontend,
  compose validate).
