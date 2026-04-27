---
title: CO₂ morphometric パラメータ
description: S_mes/S, S_c/S, f_ias, T_cw を 2D 断面 + Cellpose 細胞ポリゴン + LAB-a* 葉緑体検出で計算
category: pipelines
order: 60
---

## 計算する指標

Evans & von Caemmerer / Tosens *et al.* に倣い、以下を **2D 断面プロキシ**
として計算します。3D 補正係数 (~1.1〜1.3) をかければ Tosens 2012 の
in-vivo 値と直接比較可能。

| 記号 | 意味 | 単位 | 計算根拠 |
|---|---|---|---|
| $S_\text{mes}/S$ | 葉肉細胞の IAS 露出長 / 葉断面長 | 無次元 | adjacency 法 |
| $S_c/S$ | 葉緑体が IAS に面している長さ / 葉断面長 | 無次元 | exposed-boundary 法 |
| $f_\text{ias}$ | 細胞間隙率 | 無次元 | 1 − 細胞被覆率 |
| $T_\text{cw}$ | 細胞壁厚プロキシ | µm | gap distance transform |

## 数学的定義（実装に厳密対応）

### S_mes/S — adjacency 形式

実装 (`pipeline/morphometrics_co2.py::_exposed_cell_boundary`) は
$L_\text{mes,IAS}$ を **mesophyll 内の Cellpose 細胞マスク $C$ と、
膨張した IAS マスク $\delta(\text{IAS})$ の AND** として測ります:

$$
B_\text{exposed} \;=\; (C \cap \Omega_\text{mes})\, \cap\, \delta(\Omega_\text{IAS})
$$

$$
\frac{S_\text{mes}}{S} \;=\; \frac{|B_\text{exposed}|}{L_\text{leaf,px}}
$$

ここで $L_\text{leaf,px}$ は mesophyll 領域の **minAreaRect 長軸** の
画素長 (`_mesophyll_section_length_ds_px`)。`um_per_px` の有無に
依らず両者ピクセル単位なので**無次元**で出ます。
細胞 - 細胞 間の壁、細胞 - 表皮 間の壁は IAS に接していないので
自動的に除外される構成 (Thain 1983; Evans & Loreto 2000)。

### S_c/S — exposed boundary に乗っている葉緑体

$$
\frac{S_c}{S}
\;=\; \frac{|B_\text{exposed}\, \cap\, \delta(\text{chloroplast},\,r=2)|}
{L_\text{leaf,px}}
$$

葉緑体マスクを 2 px 膨張させて IAS 露出細胞境界と AND を取ることで、
**実際にガス相と接する葉緑体壁長** だけをカウント。生の葉緑体周囲長
を使うと細胞内側のラインが過大評価される問題を回避。

### f_ias — 細胞被覆率の補数

$$
f_\text{ias} \;=\; 1 - \frac{|U(C \cap \Omega_\text{mes})|}{|\Omega_\text{mes}|}
$$

$U(\cdot)$ は **ラスタライズした和集合** で、Cellpose インスタンスの
重複を除去。$[0, 1]$ に自然に収まり clamp 不要。

### T_cw — 細胞間隙の距離変換

$$
\text{DT}_\text{gap}(p) \;=\; \min_{q \in C \cap \Omega_\text{mes}} \|p - q\|
\quad \text{for}\ p \in \Omega_\text{IAS} \cap \Omega_\text{mes}
$$

距離変換の **mean / median / 95%tile** を `T_cw_mean / T_cw_median / T_cw_p95`
として返します（µm 換算と px 両方）。

## 葉緑体検出（classical CV）

LAB 色空間の **a\* チャンネル**で Otsu 二値化、各 Cellpose 細胞内に
クリップ。緑色色素のコントラスト指標 (a* レンジ) が `MIN_A_CHANNEL_CONTRAST = 8`
未満なら検出をスキップ（H&E 染色などの非生体色画像対策）。
学習型に差し替えやすいよう、出力 JSON のキーは固定。

## API

```http
POST /images/{image_id}/analyze/co2-morphometrics
Authorization: Bearer <supabase-jwt>
Content-Type: application/json

{
  "max_side_px": 1024,
  "chloroplast": {
    "min_area_px": 6,
    "max_area_ratio": 0.8
  }
}
```

**前提**: 同じ画像で `segformer_tissue` と `cellpose_cells` が完了
していること。両方の最新 `done` 行を読み込んで処理します。

## レスポンス

```jsonc
{
  "kind": "co2_morphometrics",
  "result": {
    "source_class": ["palisade", "spongy"],
    "downsample_factor": 1.0,
    "um_per_px": 0.42,
    "image_shape": { "height_px": 1024, "width_px": 1536 },

    // フラットなトップレベルスカラー (compare.py METRICS が直接拾う)
    "s_mes_s": 14.8,
    "s_c_s":    9.2,
    "f_ias":    0.28,

    // ネストされた詳細
    "mesophyll": {
      "area_px": 648491,
      "area_um2": 1.14e8,
      "thickness_mean_um": 168.3,
      "thickness_median_um": 175.2,
      "section_length_um": 800.4,
      "section_length_px": 1905.7
    },
    "mesophyll_cells": {
      "count": 812,
      "perimeter_total_um": 4_213.5,
      "perimeter_total_px": 10_032.1,
      "area_total_um2": 9.8e7,
      "area_total_px": 555_321,
      "mean_perimeter_um": 5.19,
      "mean_area_um2": 1.21e5
    },
    "chloroplasts": {
      "count": 1284,
      "total_area_px": 95_117,
      "total_area_um2": 1.68e7,
      "mean_area_um2": 1.31e4,
      "total_perimeter_um": 2_012.4,
      "coverage_of_mesophyll_cells": 0.172,
      "detection_method": "lab_a_otsu",
      "a_channel_contrast": 31.2
    },
    "cell_wall": {
      "t_cw_mean_um": 0.31,
      "t_cw_median_um": 0.22,
      "t_cw_p95_um": 0.48,
      "t_cw_mean_px": 0.74,
      "t_cw_median_px": 0.52,
      "t_cw_p95_px": 1.14,
      "gap_pixel_count": 76_311
    },
    "chloroplast_overlay_png_base64": "iVBORw0KG...",
    "notes": []
  }
}
```

> **mesophyll の thickness_p5_um / thickness_p95_um は実装に存在しない**。
> 代わりに `thickness_mean_um` / `thickness_median_um` を使ってください。
> `cell_wall.t_cw_p95_um` の方は提供されています。

## C3 / C4 で期待される差

| 指標 | C3 レンジ | C4 レンジ | 背景 |
|---|---|---|---|
| $S_\text{mes}/S$ | 8 – 22 | 3 – 8 | C4 は bundle-sheath 主体で葉肉薄い |
| $f_\text{ias}$ | 0.15 – 0.45 | 0.05 – 0.20 | C4 mesophyll が密 |
| $T_\text{cw}$ | 0.1 – 0.5 µm | 0.15 – 0.6 µm | C4 壁は厚め |

> **注意** — $S_\text{mes}/S$ は 3D 真値 (Tosens 2012) で `×1.2` 前後の
> 補正係数がかかります。本ツールの値は 2D プロキシなので、
> **グループ間の相対比較** に使うのが安全。

## 後段 — co2_diffusion との連携

`chloroplast_overlay_png_base64` (透明 PNG, 葉緑体画素を非ゼロ alpha
で塗ったもの) を `co2_diffusion` がそのまま読み取り、Michaelis-Menten
反応項を「葉緑体ピクセルだけ」に局在させた sink マスクとして使います。
co2_morphometrics を実行していないと `co2_diffusion` は
`palisade ∪ spongy` の合成 mesophyll マスクをフォールバックとして使い、
sink_class が `"mesophyll_cells"` で報告されます。
