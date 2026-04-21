# tools_for_plants_research

植物組織画像（顕微鏡切片）の全自動解析ツール。葉の厚み・葉肉厚・組織クラス分割・細胞検出・**水経路 / 透水マップ** までを Web UI 上で生成する。

## アーキテクチャ

```
┌─────────────┐  REST   ┌──────────────────────┐  SQL   ┌────────────────────────┐
│  Next.js 14 │◄───────►│  FastAPI (uvicorn)   │◄──────►│  Supabase self-host     │
│  (frontend) │         │  + ML pipeline        │        │  (Postgres + Auth +     │
│             │         │  asyncio.to_thread    │        │   PostgREST + Storage + │
└─────────────┘         │  for CPU-bound runs   │        │   Kong + Studio)        │
       │                └──────────────────────┘        └────────────────────────┘
       │                          ▲
       │                          │ user JWT
       │                          ▼
       │                ┌──────────────────────┐
       └───────────────►│  Supabase REST       │   ◄── RLS enforced end-to-end
            JWT          │  (PostgREST + Kong) │
                        └──────────────────────┘
                                 ▲
                                 │ named volumes (cache)
                          ┌──────┴───────┐
                          │ plants-ml    │  Cellpose cyto3 (~26 MB)
                          │ plants-hf    │  HF transformers cache
                          └──────────────┘
```

- **Frontend**: Next.js 14 App Router + TypeScript + Tailwind + shadcn-ish + Konva (annotations) + React Zoom Pan Pinch
- **Backend**: FastAPI 0.115 + Pydantic v2 + supabase-py (httpx wrapper for RLS-aware reads/writes via the caller's JWT)
- **ML**: classical CV (OpenCV + numpy + scipy) + Cellpose 3 + HuggingFace transformers (SegFormer) + scikit-fmm
- **DB / auth / storage**: Supabase self-hosted; idempotent post-init bootstrap container

> **Required tooling**: GNU make 4+ (default on Linux; on macOS install via `brew install make` if `make --version` reports something older).  Docker Desktop 4.30+, Docker Compose v2.20+.

## クイックスタート

```bash
# 1) 環境ファイル
make init-env                                 # cp .env.example .env

# 2) JWT 秘密鍵を生成（.env の JWT_SECRET に貼る）
openssl rand -base64 48 | tr -d '\n'

# 3) anon / service_role キー（手順 2 の値を引数に）
make gen-jwt                                  # ANON_KEY / SERVICE_ROLE_KEY が出る → .env に貼る

# 4) POSTGRES_PASSWORD / DASHBOARD_PASSWORD も書き換える
#    NEXT_PUBLIC_SUPABASE_ANON_KEY = ANON_KEY と同じ値

# 5) 起動 + 動作確認
make up
make smoke                                    # /health / /analyze/segformer/status / フロント

# 6) ブラウザ
open http://localhost:3000                    # アプリ
open http://localhost:3001                    # Supabase Studio
```

`make help` で全 30+ ターゲット一覧が見える。

## エンドツーエンドのワークフロー

```
[1] 画像アップロード ─→ images テーブル + Storage
       │
       ▼
[2] アノテーション (任意)         ─→ annotations テーブル（ポリゴン）
       │
       ▼
[3] 学習データエクスポート ─→ training/export.zip
       │
       ▼
[4] notebooks/segformer_train.ipynb ─→ models/segformer/
       │
       ▼
[5] SegFormer 推論 (UI)           ─→ analyses(kind=segformer_tissue)
       │
       ▼
[6] Cellpose 細胞検出 (任意, UI)   ─→ analyses(kind=cellpose_cells)
       │
       ▼
[7] 水経路 (UI)                   ─→ analyses(kind=water_path) + ヒートマップ
```

詳細は各機能の節を参照。

## マイルストーン

| PR | 内容 | 状態 |
|----|------|------|
| #1 | インフラ土台 | ✅ |
| #2 | 認証 + 画像アップロード + ビューワー | ✅ |
| #3 | スケール検出 + 葉領域抽出 + 基本計測 | ✅ |
| #4 | ブラウザ内アノテーションワークフロー | ✅ |
| #5a | ポリゴン→マスクのラスタライズ + 学習データエクスポート | ✅ |
| #5b | Cellpose で細胞検出 | ✅ |
| #5c | SegFormer 推論 + 訓練ノートブック | ✅ |
| #6 | 最短経路 + 透水マップ | ✅ |
| #7 | 精度検証 + リリース整備（本PR） | 🔨 |

## 機能

### 基本計測（PR #3）

- 右下 ROI のスケールバー検出 → µm/px キャリブレーション
- HSV Saturation + Otsu で葉領域抽出
- 各列の min/max y で厚みプロファイル（最大 512 点）
- summary stats は全列で計算（≠ 間引き後）
- UI: 純 SVG 折れ線チャート + CSV ダウンロード

### アノテーション（PR #4）

- Konva ベースの pan/zoom + ポリゴン入力
- クリックで頂点、Enter 確定、Backspace 1 点戻す、Esc 取消、Space 押しながらドラッグでパン
- 既存ポリゴンクリックで（自分のもののみ）削除
- DB 側で `class` enum + `is_valid_polygon(jsonb)` CHECK で整合性保証
- RLS: 画像が読めるユーザは閲覧 OK、insert/update/delete は所有者のみ

### 学習データエクスポート（PR #5a）

- `pipeline/rasterize.py`: ポリゴン → uint8 PNG（0=背景、1..N=クラス）、last-write-wins
- `GET /training/export.zip` で `images/` + `masks/` + `classes.json` + `index.json` を bundle
- 「未ラベルも含める」トグル付き
- マスクは SegFormer の `AutoImageProcessor` がそのまま受け取れる形

### Cellpose 細胞検出（PR #5b）

- Cellpose 3 cyto3 generalist model（重み ~26 MB、初回 DL 後は volume cache）
- 1024 px に down-sample → 推論 → 原座標にスケール戻し
- per-cell polygon + centroid + area_px、mean/median 統計
- BackgroundTask + `asyncio.to_thread` で event loop ブロック回避
- UI: terminal-gated polling（transient 失敗で停止しない）+ SVG polygon overlay

### SegFormer 組織分割（PR #5c）

- ユーザーが `notebooks/segformer_train.ipynb` で訓練 → `models/segformer/` に置く
- 起動時 probe: `GET /analyze/segformer/status` で checkpoint の有無 + 完全性 (`config.json` + `preprocessor_config.json` + weights) を返す
- 完全性チェックを probe と POST 検証で同期、不完全な checkpoint で job を queue しない
- 推論: 1024 px down-sample → bilinear upsample → argmax → `cv2.RETR_CCOMP` で hierarchy 付き polygon 抽出
- UI: クラス別面積表 + SVG polygon overlay（hole 有りは `<path fill-rule="evenodd">` で穴抜き）

### 水経路 / 透水マップ（PR #6）

- 入力: SegFormer 結果（クラス別 polygon + image_shape）
- ソース: `xylem_vessel`（導管）優先、無ければ `xylem` (木部) にフォールバック
- シンク: `stomata` polygon centroid（absorbing boundary ではなく、サンプル点）
- コスト場: クラス別水流抵抗（`DEFAULT_RESISTANCE`、リクエストで override 可、サニタイズ付き）
- `skfmm.travel_time(phi, speed=1/cost, dx=inv_factor)` で原画像 px 単位の travel time を計算
- 各シンクから FMM 場の勾配降下で polyline を辿る、source 未到達時は Euclidean 最近接に snap（UI で破線表示）
- UI: マグマヒートマップを `mix-blend-screen` で重ね描画 + polyline + 各種統計
- live SegFormer-availability probe（mount + on focus + 10s poll、viewer は probe なし）

## 精度検証（PR #7）

旧手動ツールで作った `measure_results.xlsx` を ground truth として、basic_measurement の出力との誤差レポートを生成。

```bash
# stack を起動 + 検証対象画像を UI からアップ
make up

# 認証は 2 通り：
#  (a) password 持ちアカウント → メール指定（プロンプトで入力）
VALIDATE_EMAIL="you@example.com" make validate
#  (b) magic-link アカウントは password が無いので、ブラウザ DevTools
#      → Application → Local Storage → sb-...-auth-token から
#      access_token を取り出して環境変数で渡す
VALIDATE_TOKEN="eyJhbGciOi..." make validate

# → outputs/validation_report.md + .json が cwd 直下に生成される
```

`make validate` は `.env` を自動 source するので `ANON_KEY` 等は事前 export 不要。スクリプトは N 行に拡張可能。`測定タイプ=Distance` `メモ=厚さ` → `leaf_mean_thickness_um`、`メモ=葉肉の厚さ` → `leaf_median_thickness_um` のマッピングは `--metric-map '{...}'` で上書き可能。`維管束面積` は basic_measurement では出ないので N/A（SegFormer の xylem polygon 面積で別途比較）。

## データモデル / 権限

| テーブル | 用途 | RLS 概要 |
|---|---|---|
| `profiles` | `auth.users` と 1:1 | read = authenticated 全員 / update = 自分 |
| `images` | 画像メタ | private = 所有者 / lab = authenticated / public = anon |
| `analyses` | パイプライン結果 (basic / cellpose / segformer / water_path) | image RLS に委譲 |
| `annotations` | 手動ポリゴン | 画像が読めれば read、書き込みは所有者 |

Storage バケット `images` にも同じ可視性を `storage.objects` の RLS で適用。

## 認証フロー

1. `/login` でメールアドレスを入れる → magic link 送信
2. 開発環境では SMTP 未設定なので `make logs-auth` で出力されたリンクを直接踏む
3. `/auth/callback` で code 交換 → `/dashboard`

## 運用 / デプロイのヒント

- 研究室サーバへ持っていく場合は `.env` の `SITE_URL` / `NEXT_PUBLIC_SITE_URL` / `SUPABASE_PUBLIC_URL` / `BACKEND_CORS_ORIGINS` をその FQDN に書き換え、リバースプロキシ（caddy / nginx）で 3000/8001/8000/3001 を集約する
- 本物の SMTP を使うなら `.env` の `SMTP_*` を埋める（`make restart` で反映）
- GPU が使える環境なら Cellpose / SegFormer の `gpu=False` を `True` にして再ビルド（数倍速くなる）
- Supabase Cloud に移行する場合は `SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_URL` / `ANON_KEY` / `SERVICE_ROLE_KEY` を Cloud のものに差し替えるだけで済む（self-host と同じ API contract）

## ディレクトリ構成

```
tools_for_plants_research/
├── Makefile                              # make help で全ターゲット
├── CHANGELOG.md
├── docker-compose.yml                    # backend + frontend
├── docker-compose.supabase.yml           # Supabase self-host 一式
├── .env.example
├── backend/                              # FastAPI
│   ├── app/
│   │   ├── api/                          # REST ルート
│   │   ├── core/                         # 設定 + Supabase httpx クライアント
│   │   └── pipeline/                     # 解析パイプライン
│   │       ├── classes.py                # 共通組織クラス
│   │       ├── scale.py / segment.py / measure.py
│   │       ├── rasterize.py
│   │       ├── cellpose_infer.py
│   │       ├── segformer_infer.py
│   │       └── water_path.py
│   └── tests/
├── frontend/                             # Next.js 14
│   ├── app/
│   ├── components/                       # *Panel.tsx + AnnotationEditor + ...
│   └── lib/
│       ├── supabase/                     # client / server / middleware / types / public-url
│       └── tissue-classes.ts             # ⇄ backend/app/pipeline/classes.py
├── models/                               # SegFormer checkpoint 配置先（gitignore）
│   └── README.md
├── notebooks/
│   └── segformer_train.ipynb
├── scripts/
│   ├── generate-jwt.sh
│   └── validate_against_xlsx.py
├── volumes/
│   ├── api/kong.yml                      # Kong ルーティング + CORS
│   └── db/init/                          # initdb 用 SQL
└── docs/
```

## 将来計画

- 研究室機材 → クラウドへの直接アップロード（`images.source = device:<serial>` の枠は確保済み）
- 外部公開用の OAuth プロバイダ追加
- FiPy による Darcy 流の本格 PDE シミュレーション
- 生育条件メタデータ + 系統間比較ダッシュボード

## ライセンス

未定。研究室内利用想定。
