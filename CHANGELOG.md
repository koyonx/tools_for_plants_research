# Changelog

All notable changes to **tools_for_plants_research**.  Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project
hasn't tagged a release yet, so everything below is on `main`.

## [Unreleased]

### PR #13a — CO2 反応拡散 PDE ソルバ (current)
- `backend/app/pipeline/co2_diffusion.py`: 定常反応拡散方程式
  `∇·(D∇C) - r·C = 0` を有限体積で解く。Dirichlet BC は気孔で C=Ci、
  反応項 r·C は葉緑体セル (または葉肉セル) で有効。scipy.sparse +
  spsolve、harmonic-mean face conductivity、signed 面フラックスで
  A_net と g_m_proxy = A_net / (Ci - Cc) を算出。
- `backend/app/api/co2_diffusion.py`: `POST /images/{id}/analyze/co2-diffusion`
  + バックグラウンド solve。segformer_tissue 完了が必須、
  co2_morphometrics はオプション (あれば葉緑体オーバーレイを吸収域
  に使用、無ければ葉肉細胞フォールバック)。
- `batches.py`: `co2_diffusion` を SUPPORTED_KINDS / _PIPELINE_EXEC_ORDER
  に追加 (co2_morphometrics の後、最終)。
- `compare.py`: 4 メトリクス追加 (g_m_proxy, cc_mean_pa,
  drawdown_mean_pa, a_net)。
- `Co2DiffusionPanel.tsx` を画像詳細ページに搭載 (CO₂ morphometrics
  の下)。濃度場 ↔ 降下場トグル、気孔別降下量を半径エンコード可視化。
- 13 pytest ケース: ヘッダー入力バリデーション、pure Fick (r=0)
  → uniform Ci、反応 → Cc < Ci、monotonicity (r 増加で drawdown 増加)、
  override sanitisation、葉緑体オーバーレイ使用、気孔別降下、
  Dirichlet 隣接の flux gating、strict JSON。
- PR #13b で Farquhar A-Cc fit + LI-COR 実測との突き合わせを追加予定。

### PR #12 — Darcy 水流 PDE ソルバ
- `backend/app/pipeline/darcy.py`: 葉組織マスクを境界条件に
  steady-state Darcy: ∇·(K∇P)=0 を有限体積で解く。scipy.sparse
  + spsolve、harmonic-mean face conductivity (組織境界の K
  不連続を正しく扱う)、Dirichlet BC を xylem (P_xylem) と stomata
  (P_stomata) に課し leaf 外は no-flow。出力: 圧力場 PNG、流速場
  PNG、K_leaf (kg/(s·Pa·m))、平均/95%tile/最大流速、xylem→stomata
  総流量 (連続性チェック付き)、気孔別流出量。
- `backend/app/api/darcy.py`: `POST /images/{id}/analyze/darcy` +
  バックグラウンド solve。`segformer_tissue` 完了が前提。BC圧力
  と per-class permeability override を受け付け、サニタイズ後
  スレッドプールで実行。
- `batches.py`: `darcy_flow` を `SUPPORTED_KINDS` /
  `_PIPELINE_EXEC_ORDER` に追加 (water_path の後、co2_morphometrics
  の前)。バッチ dispatcher 経由でも実行可能。
- `compare.py`: 5 つの新メトリクス (`darcy_k_leaf`,
  `darcy_mean_velocity`, `darcy_p95_velocity`,
  `darcy_total_flow_out`, `darcy_pressure_drop_pa`) を METRICS
  カタログに追加。`metrics` フィールドの max_length を 20 → 40
  に拡張。
- `DarcyPanel.tsx` を画像詳細ページに搭載 (water_path の下、CO₂
  morphometrics の上)。圧力場 ↔ 流速場のトグル付きオーバーレイ、
  気孔別流出量を半径エンコードで可視化、結果テーブル。
- `ImageBatchPicker` の pipeline チェックボックスに追加。
- 13 pytest ケース: 1D 解析解 (Darcy 法則) との一致 (rel<10%)、
  permeability スケール線形性、xylem_vessel/xylem fallback、
  ambiguous 入力エラー、strict JSON ラウンドトリップ、気孔別
  流出量レポート、悪い override の silent drop。

### PR #11 — LI-COR ガス交換ファイル取り込み
- `backend/app/pipeline/licor_parse.py`: 機種・列レイアウト自動判定式
  パーサー。LI-6400 / LI-6800 .xlsx 双方 + 任意の CSV/TSV を対応。
  ヘッダー行は既知エイリアス (Photo/A/Cond/gsw/Ci/...) のスコア最高行
  で検出、LI-6800 のユニット行を自動スキップ。未知列は `raw` JSON
  に温存して将来の PDE / A-Ci フィットから再利用可能。NaN/Inf は
  None に正規化して strict JSON 互換を保証。pandas 不採用、openpyxl
  + 標準 csv のみ。
- `backend/app/api/gas_exchange.py`: 4 つのエンドポイント
  (`POST /gas-exchange/upload` 多パート、`GET /sessions` 絞込、
  `GET /sessions/{id}` 点列付詳細、`DELETE /sessions/{id}`)。
  25 MB 上限、所有者解決、insert失敗時のロールバック。
- DB マイグレーション (`02-after-services.sql.tmpl`):
  `gas_exchange_sessions` (instrument CHECK、photosynthesis_type CHECK、
  RLS owner-only) + `gas_exchange_points` (owner_id 非正規化、
  ON DELETE CASCADE、Range pagination 対応)。
- `SupabaseAuthedClient` に gas_exchange CRUD ヘルパー追加、
  list_gas_exchange_points は PostgREST 1000 行制限を回避する
  Range header pagination。
- 11 pytest ケース: LI-6400 TSV / LI-6800 xlsx / カスタム CSV /
  ヘッダー行不在エラー / マジックバイト判定 / 旧 .xls 拒否 /
  NaN-Inf 正規化 / 末尾空行 / Obs+timestamp 列の正しい抽出 /
  複数シート最高スコア選択 / strict JSON ラウンドトリップ。
- フロント: `/dashboard/gas-exchange` ページ (アップロード + 絞り込み
  一覧 + 詳細 + A/Ci SVG 散布図 + 点列テーブル)、画像詳細ページに
  同 plant_id のセッションへのクロスリンク、ヘッダーに「ガス交換」
  リンク追加。
- pyproject: `openpyxl>=3.1` を `dev` extra から本体 `dependencies`
  に昇格 (アップロードエンドポイントで実行時必須)。

### PR #10 — CO2 diffusion morphometrics
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
