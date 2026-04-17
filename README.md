
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
| #1 | インフラ土台（本PR） | 🔨 進行中 |
| #2 | 認証 + 画像アップロード + ビューワー | ⏳ |
| #3 | スケール検出 + 葉領域抽出 + 基本計測 | ⏳ |
| #4 | アノテーションワークフロー | ⏳ |
| #5 | 組織多クラス分割 + 気孔/維管束検出 | ⏳ |
| #6 | 最短経路 + 透水マップ | ⏳ |
| #7 | 精度検証 + リリース整備 | ⏳ |

## 将来計画

- 研究室内の機材からクラウド（Supabase Storage）への直接アップロード
- 画像の可視性（private / lab / public）管理を RLS で実装
- 外部公開時の OAuth ログイン対応
