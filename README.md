
# tools_for_plants_research

植物組織画像（顕微鏡切片）の全自動解析ツール。葉の厚み・葉肉厚・維管束面積・気孔と維管束間の距離・水の通りやすさマップまでを Web UI 上で生成する。

## アーキテクチャ

```
┌─────────────┐       ┌─────────────┐       ┌──────────────────┐
│  Next.js    │◄─REST─►│  FastAPI    │◄─SQL─►│ Supabase         │
│  (frontend) │       │  (backend)  │       │ (Auth/DB/Storage)│
└─────────────┘       └─────────────┘       └──────────────────┘
         ▲                     ▲                     ▲
         └────── docker compose (Apple Silicon 対応) ────┘
```

- **Frontend**: Next.js 14 (App Router) + TypeScript + Tailwind + shadcn/ui
- **Backend**: FastAPI (Python 3.11) + Pydantic v2
- **Auth / DB / Storage**: Supabase (self-hosted → 将来 Supabase Cloud へシームレス移行)
- **ML (今後のPRで追加)**: PyTorch + SAM2 + Cellpose + scikit-fmm + FiPy

## セットアップ

### 前提
- Docker Desktop（Apple Silicon 対応版） 4.30+
- Docker Compose v2.20+ （`include:` ディレクティブ利用のため）
- `gh` CLI（任意、PR操作用）

### 手順

```bash
# 1. .env を準備
cp .env.example .env

# 2. JWT_SECRET を生成して .env に書き込む
openssl rand -base64 48 | tr -d '\n'     # 出力を JWT_SECRET= に貼る

# 3. ANON_KEY と SERVICE_ROLE_KEY を JWT_SECRET から生成
./scripts/generate-jwt.sh "<上で生成した JWT_SECRET>"
# 出力 2 行を .env に貼る

# 4. POSTGRES_PASSWORD / DASHBOARD_PASSWORD も書き換える
# NEXT_PUBLIC_SUPABASE_ANON_KEY は ANON_KEY と同じ値を入れる

# 5. 起動
docker compose up -d

# 3. 動作確認
open http://localhost:3000          # フロントエンド
curl http://localhost:8001/health   # バックエンド
open http://localhost:8000          # Supabase API (Kong)
open http://localhost:3001          # Supabase Studio
```

### 停止・完全削除

```bash
docker compose down          # コンテナ停止（データは残る）
docker compose down -v       # ボリュームごと削除（DBリセット）
```

## ディレクトリ構成

```
tools_for_plants_research/
├── docker-compose.yml             # 本プロジェクトのサービス（backend, frontend）
├── docker-compose.supabase.yml    # Supabase セルフホスト一式
├── .env.example
├── backend/                       # FastAPI
│   ├── app/
│   │   ├── api/        # REST ルート
│   │   ├── core/       # 設定・Supabase クライアント
│   │   └── pipeline/   # 画像解析パイプライン（後続 PR で実装）
│   └── tests/
├── frontend/                      # Next.js
│   ├── app/
│   ├── components/
│   └── lib/
├── scripts/
│   └── generate-jwt.sh            # Supabase anon/service JWT 生成
├── volumes/
│   ├── api/kong.yml               # Kong ルーティング設定
│   └── db/init/                   # Postgres 初期化 SQL
└── docs/
```

## マイルストーン

| PR | 内容 | 状態 |
|----|------|------|
| #1 | インフラ土台 | ✅ マージ済 |
| #2 | 認証 + 画像アップロード + ビューワー | ✅ マージ済 |
| #3 | スケール検出 + 葉領域抽出 + 基本計測 | ✅ マージ済 |
| #4 | ブラウザ内アノテーションワークフロー | ✅ マージ済 |
| #5a | ポリゴン→マスクのラスタライズ + 学習データエクスポート | ✅ マージ済 |
| #5b | Cellpose で細胞/気孔候補の自動検出 | ✅ マージ済 |
| #5c | SegFormer 推論 + 訓練ノートブック | ✅ マージ済 |
| #6 | 最短経路 + 透水マップ（本PR） | 🔨 進行中 |
| #7 | 精度検証 + リリース整備 | ⏳ |

## 解析パイプライン（PR #3）

Classical CV のみで実装（深層学習は PR #5 以降）:

1. **スケールバー検出** — 画像右下 30% × 15% の ROI で Otsu 二値化、水平方向に
   morphology close、最も幅の大きい connected component を採用。ユーザーが指定した
   µm 値と px 幅から `um_per_px` を算出。
2. **葉領域抽出** — HSV の Saturation に Otsu 適用、morphology open/close の後
   最大連結成分を葉マスクとして採用。
3. **計測** — 各列の min/max y から縦方向の厚み px を取り、`um_per_px` で µm に換算。
   葉面積はマスク画素数 × (µm/px)²。

バックエンドエンドポイント:
- `POST /images/{id}/analyze` — 上記を同期実行し結果を `analyses` テーブルに保存
- `GET /analyses/{id}` — 結果取得
- `GET /analyses/{id}/csv` — サマリ + 厚みプロファイルを CSV で出力

UI:
- 画像詳細ページに「基本計測」パネル。スケール長 (µm) を入れて「解析する」を押すと
  結果と厚みプロファイル SVG チャートが表示される。CSV ダウンロード可。

## アノテーション（PR #4）

Web 完結のポリゴンエディタ。napari は使わず、研究室内複数人がブラウザから直接ラベル付けできる。

- `/dashboard/images/[id]/annotate` に Konva ベースのキャンバスエディタ
- クラスは `frontend/lib/tissue-classes.ts` と `backend/app/pipeline/classes.py` で共通管理
  （表皮 / 柵状 / 海綿状 / 維管束鞘 / 木部 / 師部 / 気孔 / 細胞間隙 / その他）
- クリックで頂点追加、Enter で確定、Backspace で 1 点戻る、Esc で取消、ホイール/ドラッグで zoom/pan
- 既存ポリゴンをクリックすると自分のものなら削除
- `annotations` テーブルに polygon を JSON で保存（ピクセル座標、image_id にひも付け）
- PR #5 でバックエンドが polygon をラスタライズして SegFormer の学習データに変換

## 水経路 / 透水マップ（PR #6）

PR #5c の SegFormer 結果から導管 (`xylem_vessel`、無ければ `xylem`) をソース、気孔 (`stomata`) をシンクとし、組織クラスごとの水流抵抗を持つコスト場上で `scikit-fmm` (Fast Marching Method) を解いて travel time マップ + 各気孔への最短経路を算出する。

- **新クラス**: `xylem_vessel` (導管) を追加。アノテーション・SegFormer 学習で使えば、より精度の高い水経路解析が可能。無ければ `xylem` (木部) で代用
- **コスト場**: クラス別水流抵抗（DEFAULT_RESISTANCE）を `pipeline/water_path.py` で定義、リクエスト body から override 可能
  - 既定値: vessel 0.05, xylem 0.1, palisade 1.0, spongy 1.2, intercellular 0.6, stomata 0.2, epidermis 8.0, …
  - 葉外の背景は 100.0 (実質的な壁)
- **endpoints**:
  - `POST /images/{id}/analyze/water-path` (要 SegFormer 結果完了 → 412 を返す)
- **Frontend**: 画像詳細ページに WaterPathPanel
  - 導管/木部からの travel time をマグマ系の半透明ヒートマップで重ね描画
  - 各気孔と最近接ソースを SVG 線分でハイライト
  - 平均/中央/最小/最大 travel time を表示

## SegFormer 組織分割（PR #5c）

PR #4 のアノテーションを学習データに変換（PR #5a）→ SegFormer (`nvidia/mit-b0`) を fine-tune → 得た checkpoint を backend がドロップインで読んで UI から推論できる。深層学習本体はユーザー側で訓練、推論はアプリに内蔵という分担。

- **訓練**: `notebooks/segformer_train.ipynb`（ダッシュボードからダウンロードした `plants-research-training.zip` をそのまま入力、出力は `models/segformer/`）
- **Backend**: `pipeline/segformer_infer.py`（遅延シングルトン model、up-sample argmax → 各クラスの `findContours` → polygon + coverage 統計）
- **Endpoints**:
  - `GET /analyze/segformer/status` → checkpoint の有無を返す（UI の state 判定用）
  - `POST /images/{id}/analyze/segformer` → `analyses(kind='segformer_tissue')` 行 + BackgroundTasks + `asyncio.to_thread`
- **UI**: `SegFormerPanel` がクラス別の面積 (µm² or px²) + coverage 比率を表でまとめつつ、タイソークラス色の透過 polygon を画像にオーバーレイ
- **運用**: checkpoint 未配置時は UI が「checkpoint 未配置」と表示、backend は 503 を返す（Cellpose / 基本計測は独立で動作）

## Cellpose 細胞検出（PR #5b）

Cellpose 3 の cyto3 generalist モデルを使って、1 クリックで細胞単位のセグメンテーション（細胞数・平均面積・個別ポリゴン）が取れる。

- **Backend**: `pipeline/cellpose_infer.py`（遅延シングルトンで model 読み込み、最大辺 1024px に down-sample → 原座標にスケール戻し）
- **Endpoint**: `POST /images/{id}/analyze/cellpose` → `analyses` 行に `kind='cellpose_cells'`, `status='running'` で insert し、FastAPI BackgroundTasks で推論本体を走らせる
- **Polling**: フロントは `GET /analyses/{id}` を 2.5s 間隔でポーリング、`done` / `error` で停止
- **UI**: 画像詳細ページに「Cellpose 細胞検出」パネル。完了後は細胞数・平均/中央面積（スケール有りなら µm²）と、検出ポリゴンを透過 SVG で重ねた overlay を表示
- **Docker**: PyTorch (CPU wheel) + Cellpose はランタイム image のみに載せる (`pyproject` の `ml` extra)。CI の lint/type/test レイヤは軽いまま
- **モデルキャッシュ**: `plants-ml-cache` 名前付きボリュームで `/root/.cellpose` を永続化（初回推論時に ~26MB の cyto3 重みを DL）

M2 Mac（CPU）で 1 枚 30〜60 秒程度。GPU (CUDA/MPS) があれば数秒。

## 学習データエクスポート（PR #5a）

PR #4 で保存したポリゴンアノテーションをラスタライズし、SegFormer / DeepLab などの semantic-segmentation トレーナに直接流し込める形で取り出せる。

- **マスク形式**: `uint8` の 1 チャネル PNG、0 = 背景、1..10 がクラスインデックス（`backend/app/pipeline/rasterize.py::CLASS_INDEX`）
- **重なり**: 後から描いたポリゴンが上書き（last-write-wins）
- **エンドポイント**
  - `GET /images/{id}/mask.png` — 1 枚の最新マスクを PNG で返す（エディタ画面から「マスクをダウンロード」で取得可）
  - `GET /training/export.zip[?include_unlabelled=true]` — 閲覧可能な全画像を `images/<uuid>.<ext>` + `masks/<uuid>.png` で束ね、`classes.json`（クラスインデックス定義）と `index.json`（画像ごとのメタ情報）を同梱
- **認可**: エンドポイントは caller の Supabase JWT を受け取り、RLS 経由でフィルタ（他人の private 画像は出てこない）
- ダッシュボード右上の「学習データ zip」ボタンで一括ダウンロード

## データモデル / 権限

- `public.profiles` — `auth.users` と 1:1（初回サインインで自動作成）
- `public.images` — 画像メタデータ。`visibility` は `private / lab / public` の 3 値
- Row Level Security で以下を強制:
  - `private`: 所有者のみ読み書き
  - `lab`: ログイン済ユーザー全員が read 可、書き込みは所有者のみ
  - `public`: 未ログインでも read 可
- Storage バケット `images` にも同じ可視性ロジックを `storage.objects` の RLS で適用

## 認証フロー

1. `/login` でメールアドレスを入力 → マジックリンク送信
2. 開発環境では SMTP 未設定のため、`supabase-auth` コンテナのログにリンクが出力される
   ```bash
   docker compose logs supabase-auth | grep -i "confirm your signup\|magic link"
   ```
3. リンクをクリック → `/auth/callback` でコード交換 → `/dashboard`

## 将来計画

- 研究室内の機材からクラウド（Supabase Storage）への直接アップロード
  （`images.source` に `device:<serial>` を格納できるスキーマは本PRで用意済）
- 外部公開時の OAuth プロバイダ追加
