---
title: 基本計測
description: スケールバーから µm/px 校正を取り、葉厚プロファイルを出す最初のパイプライン
category: pipelines
order: 10
---

## 目的

アップロードされた葉断面画像から、

1. 画像に写っているスケールバーと与えられた長さ (µm) から **µm/px 係数** を推定
2. 葉組織マスクを取り、断面方向に **葉厚プロファイル** を抽出
3. 葉断面面積、平均 / 中央 / 最小 / 最大厚みを算出

を行います。以降のすべての µm スケール指標はここで決めた校正値を参照します。

## アルゴリズム概要

```mermaid
flowchart TD
    A[入力画像] --> B[グレースケール化]
    B --> C[大津 + モルフォロジーで葉マスク]
    B --> D[水平ラインの<br/>スケールバー検出]
    D --> E{見つかった?}
    E -->|はい| F[バー画素長を計測]
    E -->|いいえ| G[reference_um を<br/>画像幅と仮定]
    F --> H[um/px 係数]
    G --> H
    C --> I[列方向スキャン]
    I --> J[厚み配列 t(x)]
    H --> J
    J --> K[レポート + CSV]
```

## 数式メモ

葉厚 $t(x)$ は、列 $x$ における葉マスクの **最上下エッジ差** から計算します。

$$
t(x) \;=\; \bigl(y_{\text{bottom}}(x) - y_{\text{top}}(x) + 1\bigr) \cdot \mu,
\qquad \mu = \frac{L_\text{ref}\,[\mu\mathrm{m}]}{\ell_\text{bar}\,[\mathrm{px}]}
$$

葉断面面積は **葉マスクの全画素の総和** に画素面積 $\mu^{2}$ を
かけて算出します（`pipeline/measure.py:81`）:

$$
A_\text{leaf} \;=\; \mu^{2} \sum_{(x,y)\,\in\,M_\text{leaf}} 1
\;=\; \mu^{2} \cdot N_\text{px}^\text{leaf}
$$

ここで $N_\text{px}^\text{leaf}$ は葉マスクのピクセル数。
内部空隙やエッジの不規則さも含めた **真の 2D 断面面積** を出すので、
列方向の thickness profile $\sum_x t(x)$ を使う近似（境界形状が
凸でないと過大評価する）より正確です。

葉厚分布の代表値は列ごとの $t(x)$ から取ります:

$$
\bar{t} = \frac{1}{n_x}\sum_x t(x),\quad
t_{0.5} = \operatorname{median}\{t(x)\},\quad
t_{\max} = \max_x t(x).
$$

## 入出力スキーマ

### Request

```http
POST /images/{image_id}/analyze
Authorization: Bearer <supabase-jwt>
Content-Type: application/json

{
  "reference_um": 200.0
}
```

### Response

```jsonc
{
  "id": "...",
  "image_id": "...",
  "kind": "basic_measurement",
  "status": "done",
  "result": {
    "scale": { "um_per_px": 0.4231 },
    "measurement": {
      "leaf_area_um2": 483200.0,
      "leaf_mean_thickness_um": 173.4,
      "leaf_median_thickness_um": 178.1,
      "leaf_min_thickness_um": 88.4,
      "leaf_max_thickness_um": 245.9,
      "thickness_profile_x_um": [/* ... */],
      "thickness_profile_um":   [/* ... */]
    }
  }
}
```

## 表示例

<!-- 実画像は public/docs-assets/basic-measurement.png に配置すると
     placeholder.svg から差し替わります。 -->

![葉厚プロファイル（プレースホルダー）](/docs-assets/placeholder.svg "ここに ThicknessChart のスクリーンショットを置く")

プロファイルが極端にギザつく場合は、画像の **二値化閾値** を見直すか、
ノイズが多いエッジを切り落とすクロップを先に行ってください。

## 文献値との比較

| 光合成タイプ | 平均葉厚 (µm) | 出典 |
|---|---|---|
| C3 | 80 – 350 | Poorter *et al.* 2009 |
| C4 | 50 – 220 | Poorter *et al.* 2009 |

このレンジは `literature_ranges.py` にも登録済みで、
画像詳細ページの **文献照合バッジ** が自動で判定します。
