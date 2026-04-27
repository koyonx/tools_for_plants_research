---
title: ユーザー操作フロー
description: 画像アップロードから文献照合付きレポート出力までの典型的な作業手順
order: 1
---

## 典型的な 1 セッション

新しい C3 / C4 比較を始めるときの流れを順序図に示します。

```mermaid
sequenceDiagram
    actor U as 研究者
    participant FE as Next.js フロント
    participant BE as FastAPI バックエンド
    participant SB as Supabase
    participant PIPE as 解析パイプライン

    U->>FE: 画像アップロード + メタデータ
    FE->>SB: storage + images insert
    FE->>BE: POST /images/{id}/analyze (基本計測)
    BE->>PIPE: 基本計測を同期実行
    PIPE-->>BE: 葉厚プロファイル
    BE-->>FE: analysis row (done)
    note over U,FE: 以下、必要なパイプラインを順に起動
    U->>FE: SegFormer / Cellpose / Darcy / CO2 拡散
    FE->>BE: POST /images/{id}/analyze/{kind}
    note right of FE: kind = cellpose / segformer /<br/>water-path / darcy /<br/>co2-morphometrics / co2-diffusion
    BE->>PIPE: バックグラウンド実行
    PIPE-->>SB: analyses row (done)
    FE->>BE: ValidationBadge 再 fetch (router.refresh)
    BE->>SB: 最新 analyses を読み込み
    BE-->>FE: within / below / above 判定
    U->>FE: 比較ダッシュボードへ
    FE->>BE: POST /compare
    BE-->>FE: 統計結果 + 文献判定
    U->>FE: Markdown / CSV エクスポート
    FE->>BE: POST /compare/export
    BE-->>FE: text/markdown or text/csv
```

## チェックリスト

操作手順をまず押さえたい場合は下記を順にクリアしてください
（GFM のタスクリストがそのまま動きます）。

- [x] プロジェクト Supabase に招待済み
- [x] `images` バケットにアップロード権限がある
- [ ] LI-COR XLSX / CSV を取得し `/dashboard/gas-exchange` で取り込み
- [ ] SegFormer の checkpoint が `/analyze/segformer/status` で `available: true` を返す
- [ ] Cellpose モデルキャッシュが `models/` 配下に降ってきている
- [ ] 比較したい C3 / C4 の **画像枚数 ≥ 5** ずつ揃っている
- [ ] `plant_id` / `treatment` / `photosynthesis_type` がメタデータに入力済み

## 役立つショートカット

<kbd>⌘</kbd> / <kbd>Ctrl</kbd> + <kbd>K</kbd> で画像検索を開けるようにしたい場合は、
将来的に `frontend/components/CommandPalette.tsx` を追加してください（未実装）。

## 推奨される撮影条件

<details>
<summary>クリックで展開</summary>

- 照度均一の透過光、倍率 40×
- スケールバー **200 µm** 以上を必ず写し込む
- 葉組織が画面の 60% 以上を占めるようクロップ
- JPEG 品質は 90 以上、WebP 可

</details>
