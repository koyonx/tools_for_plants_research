---
title: Water path（最短経路コスト）
description: 維管束から気孔までの水分子仮想移動コストを Dijkstra で推定
category: pipelines
order: 40
---

## 目的

葉肉組織の疎密・空隙率を反映した **仮想コスト場** 上で、
維管束 → 気孔の最短経路を Dijkstra で求めます。Darcy PDE
より高速で、大量の画像を用いたスクリーニング向きです。

$$
\tau(s \to t) \;=\; \min_{\pi \in \Pi(s, t)} \sum_{(u, v) \in \pi} w(u) + w(v)
$$

ここで $w$ はピクセルの通過コスト。`air_space` は低コスト、
`bundle_sheath` / `vein` は中、`epidermis` は壁として高コスト
を与えます。

## コスト関数

ピクセル $p$ のコスト $w(p)$ は組織クラス $c(p)$ から決まります。

$$
w(p) \;=\;
\begin{cases}
0.1 & c(p) = \texttt{air\_space} \\
1.0 & c(p) = \texttt{mesophyll} \\
2.0 & c(p) \in \{\texttt{bundle\_sheath},\, \texttt{vein}\} \\
10.0 & c(p) = \texttt{epidermis} \\
+\infty & \text{それ以外（背景）}
\end{cases}
$$

## アルゴリズム

```mermaid
flowchart TD
    A[SegFormer mask] --> B[source 画素 = vein]
    A --> C[sink 画素 = stoma 重心]
    A --> D[通過コスト w(p) 配列]
    B --> E[multi-source Dijkstra]
    D --> E
    C --> F[各 sink の累積コスト τ]
    E --> F
    F --> G[travel_time_mean<br/>travel_time_p50<br/>sink_count]
```

## 出力

```jsonc
{
  "kind": "water_path",
  "result": {
    "travel_time_mean": 182.4,
    "travel_time_p50":  170.9,
    "sink_count": 18,
    "source_class": "vein",
    "paths": [
      { "sink_id": 1, "polyline": [[x,y], ...], "cost": 164.2 },
      /* ... */
    ],
    "heatmap_png_base64": "iVBORw0KGg..."
  }
}
```

ヒートマップは `window.fetch(...).then(r => r.blob())` で保存すれば、
単一画像のサプリメンタル図として使えます。

## C3 / C4 比較の着地点

> **観察例** — C4 葉では bundle sheath ∘ vein の密集により
> 平均 τ が **小さく** なる傾向があります。C3 では気道長が長く、
> 同じ葉厚でも τ が 1.3〜1.8 倍になる事例が多いです（未公表観察）。

## 注意点

- Dijkstra の計算量は $O(N \log N)$。10⁶ ピクセル程度なら数秒。
- 8-近傍で接続するため、境界が斜めに走る画像でも自然な経路が出ます。
- 極端な air_space 偏在で source → sink が到達不能な場合 `cost = null` を返します。
