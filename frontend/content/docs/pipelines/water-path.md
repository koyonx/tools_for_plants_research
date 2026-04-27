---
title: Water path（Eikonal Fast Marching）
description: 木部 → 気孔の水移動到達時間を Eikonal 方程式の Fast Marching Method で計算
category: pipelines
order: 40
---

## モデル — Eikonal 方程式

葉組織を異方性媒体とみなし、木部維管束から発射された "水パケット"
の **到達時間場** $T(\mathbf{x})$ を Eikonal 方程式で計算します。

$$
\bigl|\nabla T(\mathbf{x})\bigr| \;=\; \frac{1}{F(\mathbf{x})}
\qquad\text{in }\Omega,
\quad T = 0\ \text{on}\ \Gamma_\text{xylem}
$$

| 記号 | 意味 | 単位 |
|---|---|---|
| $T(\mathbf{x})$ | source からの到達時間（コスト距離） | [resistance × length] |
| $F(\mathbf{x})$ | 局所伝播速度 = $1 / w(\mathbf{x})$ | [1 / resistance] |
| $w(\mathbf{x})$ | 組織別の通過抵抗 | [resistance / length] |

これは **Sethian (1996)** が定式化した Fast Marching Method (FMM)
で、`scikit-fmm` の `skfmm.travel_time(phi, speed=F, dx)` を呼び出して
解いています。Dijkstra と違い、

- **連続場の Eikonal 方程式の弱解** を求めるので sub-pixel 精度
- 隣接 4-cell の **upwind スキーム** で因果性を保つ
- 計算量 $O(N \log N)$、Dijkstra と同じ priority queue ベース

実装本体: `pipeline/water_path.py::compute_water_path`、
ソルバー呼び出し: `skfmm.travel_time` (`pipeline/water_path.py:300-305`)。

## 抵抗 $w$ の組織別割当

`DEFAULT_RESISTANCE` (`pipeline/water_path.py::DEFAULT_RESISTANCE`)
の代表値:

| クラス | 抵抗 $w$ | 速度 $F$ | 物性 |
|---|---|---|---|
| `xylem_vessel` | 0.1 | 10 | 導管、source 後の最速経路 |
| `xylem` | 0.2 | 5 | 木部全体 |
| `intercellular` | 0.5 | 2 | 細胞間隙 |
| `palisade` | 1.0 | 1 | 柵状葉肉、基準 |
| `spongy` | 1.0 | 1 | 海綿状葉肉 |
| `bundle_sheath` | 2.0 | 0.5 | 維管束鞘 |
| `phloem` | 5.0 | 0.2 | 篩部、糖専門 |
| `stomata` | 1.0 | 1 | sink 近傍、終点判定用 |
| `upper_epidermis` | 8.0 | 0.125 | 表皮、ほぼ壁 |
| `lower_epidermis` | 8.0 | 0.125 | 同上 |
| `other` | 3.0 | 0.33 | 既定 |
| 背景 | $\infty$ (`BACKGROUND_COST = 100.0`) | $\approx 0$ | 葉外 |

operator は API リクエストで上書き可能。

## ソース・シンク

- **source** (T = 0 を pin する開始点) — `xylem_vessel` 全画素。
  vessel が無い断面では `xylem` にフォールバック (`source_class` に記録)。
- **sink** (測定対象) — `stomata` の各連結成分の重心。
  各 sink について到達時間を返す。

```mermaid
flowchart TD
    A[SegFormer mask] --> B[抵抗 w(x) 構成]
    B --> C[速度 F = 1/w]
    A --> S[source 画素 = xylem_vessel/xylem]
    S --> P[phi 初期化<br/>source = -1, それ以外 = +1]
    C --> FMM[skfmm.travel_time]
    P --> FMM
    FMM --> T[T(x) 到達時間場]
    A --> SK[sink 画素 = stomata 重心]
    T --> EXT[各 sink の T を抽出]
    SK --> EXT
    EXT --> OUT[StomatumPath × N + 統計]
```

## 出力スキーマ

```jsonc
{
  "kind": "water_path",
  "result": {
    "image_shape": { "height_px": 1024, "width_px": 1536 },
    "source_class": "xylem_vessel",
    "stomata": [
      {
        "centroid": [123.4, 56.7],
        "travel_time": 184.2,           // 抵抗単位の累積コスト
        "travel_time_um": 92.1,         // µm 換算 (× um_per_px)
        "straight_line_um": 78.0,       // 直線距離 (基準)
        "nearest_source": [98.0, 12.0], // 起点 vessel pixel
        "route": [[x, y], ...],         // back-tracking した軌跡
        "truncated": false              // 軌跡長制限に当たったか
      },
      // ...
    ],
    "travel_time_mean": 182.4,
    "travel_time_p50":  170.9,
    "sink_count": 18,
    "heatmap_png_base64": "iVBORw0KG..."
  }
}
```

各 `StomatumPath` (`pipeline/water_path.py::StomatumPath`) は

| フィールド | 意味 |
|---|---|
| `centroid` | sink 中心 (px) |
| `travel_time` | T 値 (抵抗単位) |
| `travel_time_um` | µm 換算 (un_per_px が無いと None) |
| `straight_line_um` | 直線距離 (µm) |
| `nearest_source` | 起点 vessel 画素 |
| `route` | back-tracking した経路 polyline |
| `truncated` | 経路長 cap (15000 step) で打ち切られたか |

を持ちます。

## API

```http
POST /images/{image_id}/analyze/water-path
Authorization: Bearer <supabase-jwt>
Content-Type: application/json

{
  "max_side_px": 1024,
  "resistance": null              // null = DEFAULT_RESISTANCE を使用
}
```

## C3 / C4 比較の着地点

> **観察例** — C4 葉では bundle sheath が xylem を取り巻く Kranz 解剖
> により平均到達時間が **小さく** なる傾向。C3 では気道長が長く、
> 同じ葉厚でも T が 1.3〜1.8 倍になる事例が多い (未公表観察)。

## 実装上の注意

- 速度 `F` の最小値は数値安定のため $10^{-6}$ にクリップ
  (`pipeline/water_path.py:286`)。infinity speed は upwind スキームで
  発散するため。
- 軌跡 back-tracking は最大 15,000 ステップで打ち切り。長過ぎる経路
  は `truncated: true` で返す。
- source が空（`xylem_vessel` も `xylem` も無い）のとき
  ValueError を上げます。

## なぜ Dijkstra ではなく Fast Marching か

| 比較項目 | Dijkstra | Fast Marching (FMM) |
|---|---|---|
| 解く方程式 | グラフ最短経路（離散） | Eikonal $|\nabla T| = 1/F$（連続） |
| 精度 | 1-pixel 量子化（45°step 制限） | 連続場の弱解、sub-pixel |
| 物理的解釈 | 経路コスト最小 | 波面伝播の到達時間 |
| 異方性媒体 | 弱い | 速度場で自然に表現 |

葉組織のような **連続的な抵抗分布を持つ媒体** の travel time を
論じる文脈では FMM が物理的に正しい選択（Sethian 1999, "Level Set
Methods and Fast Marching Methods" Cambridge UP）。
