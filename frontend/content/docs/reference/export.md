---
title: レポートエクスポート
description: 比較ダッシュボードから Markdown / CSV でダウンロードする
category: reference
order: 10
---

## フォーマット

`POST /compare/export` に `format=markdown` または `format=csv` を指定。
結果は `text/markdown` / `text/csv` として直接レスポンスされます。

### Markdown 出力（例）

```markdown
# Compare Report

Generated at `2026-04-24T15:02:17Z` UTC.

## Group definitions

| Group | Filter | N images |
|---|---|---|
| A | `photosynthesis_type=C3` | 12 |
| B | `photosynthesis_type=C4` | 9 |

## Per-metric comparison

| Metric | Unit | A median | B median | Welch p | MW p | Hedges g (95% CI) | A vs lit | B vs lit |
|---|---|---|---|---|---|---|---|---|
| S_mes/S | - | 15.0 | 5.0 | 1.50e-06 | 2.30e-05 | 4.95 [3.2, 6.7] | within | within |
| f_ias   | - | 0.28 | 0.10 | 3.1e-05  | 4.2e-04  | 3.20 [2.1, 4.3] | within | within |
```

### CSV 出力

```csv
metric_key,metric_label,unit,group_a_n,group_a_median,group_a_mean,group_a_sd,group_a_lit_status,group_b_n,group_b_median,group_b_mean,group_b_sd,group_b_lit_status,welch_p,mann_whitney_p,hedges_g,hedges_g_ci_low,hedges_g_ci_high
co2_s_mes_s,S_mes/S,-,12,15.0,14.8,2.1,within,9,5.0,5.2,1.3,within,1.5e-06,2.3e-05,4.95,3.2,6.7
```

## 挙動メモ

- `N images` はクエリに一致した **画像枚数**（per-metric 非 null サンプル数では
  ない）で、publication 向けに正直な値を出します。PR #17 round-1 BLOCKER で
  修正済み。
- `Filter` 列の文字列は Markdown のセル区切りを壊さないよう
  `|`, バッククォート, 改行, `<>` を自動エスケープ。
- CSV は `csv.writer` 標準エスケープ。`None` 値は空セル（`NaN` 文字列は出ません）。

## 呼び出し例

```bash
curl -X POST https://backend/compare/export \
  -H "Authorization: Bearer $SB_JWT" \
  -H "Content-Type: application/json" \
  -o compare.md \
  -d '{
    "group_a": { "photosynthesis_type": "C3" },
    "group_b": { "photosynthesis_type": "C4" },
    "metrics": ["co2_s_mes_s","co2_f_ias","darcy_k_leaf"],
    "bootstrap_iters": 2000,
    "format": "markdown"
  }'
```

## UI からの使用

- `/dashboard/compare` の <kbd>Markdown エクスポート</kbd> / <kbd>CSV エクスポート</kbd>
  ボタンから blob ダウンロード
- `compare-report.md` / `compare-report.csv` というファイル名で保存
- そのまま supplemental info に添付できる粒度

## 拡張アイデア

今後追加したい方向性:

1. LaTeX booktabs 出力
2. JSON schema 同梱（Hedges g CI の bootstrap seed を再現できる形で）
3. Markdown 出力に mermaid flowchart の同梱（ダッシュボードのフィルタ + N の DAG 可視化）
