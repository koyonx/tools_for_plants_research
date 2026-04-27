---
title: CO₂ 反応拡散 PDE
description: 葉肉内部の CO₂ 濃度場を Michaelis-Menten Farquhar 反応項 + Picard 反復で解き、Cc・A_net・g_m proxy を算出
category: pipelines
order: 70
---

## モデル — 反応拡散 PDE

葉断面の CO₂ 濃度場を、定常状態の保存型反応拡散方程式として解きます。

$$
\nabla \cdot \bigl(D(\mathbf{x})\,\nabla C(\mathbf{x})\bigr)
\;-\; R(C(\mathbf{x}))
\;=\; 0
\qquad \text{in }\Omega_\text{leaf}
$$

| 記号 | 意味 | 単位 |
|---|---|---|
| $C$ | CO₂ 分圧 | $\mathrm{Pa}$ |
| $D$ | 組織内 CO₂ 拡散係数 | $\mathrm{m^{2}\,s^{-1}}$ |
| $R(C)$ | 局所カルボキシレーション速度 | $\mathrm{mol\,m^{-3}\,s^{-1}}$ |

境界条件:

- 気孔 (`stomata`) — Dirichlet $C = C_i$（既定 25 Pa ≒ ambient 250 ppm）
- 葉外周（背景） — 物理的にはノイマン 0 だが、背景画素の $D$ を
  数値的に微小値 (`BACKGROUND_DIFFUSIVITY = 10⁻¹⁵`) にすることで
  自動的に no-flow が成立
- それ以外の組織内部 — 連続条件（隣接面で $D$ の調和平均）

組織別の $D$ デフォルト値 (`pipeline/co2_diffusion.py::DEFAULT_DIFFUSIVITY`):

| クラス | $D$ (m²/s) | 物性 |
|---|---|---|
| `intercellular` | $1.6 \times 10^{-5}$ | 25 °C 空気中の CO₂ |
| `stomata` | $1.6 \times 10^{-5}$ | 同上（Dirichlet 面なので solve には影響しない） |
| `palisade` / `spongy` / `bundle_sheath` | $1.79 \times 10^{-9}$ | 細胞壁 + 細胞質中の水溶 CO₂ |
| `xylem` / `xylem_vessel` / `phloem` | $1.79 \times 10^{-9}$ | 同 |
| `upper_epidermis` / `lower_epidermis` | $1.79 \times 10^{-10}$ | クチクラ層で抑制 |
| `other` | $1.79 \times 10^{-9}$ | 既定 |

operator が JSON で `diffusivity_override` を渡してクラス別に書き換え可能。

## 消費項 $R(C)$ — Michaelis-Menten Farquhar（既定）

Bernacchi 2001 の速度論定数を使った FvCB Rubisco-限定形を、
**Picard 反復による非線形ソルバー** で解きます。

$$
R(C) \;=\; V_\text{cmax,vol}\;\frac{C - \Gamma^*}{C + K_\text{eff}},
\qquad
K_\text{eff} \;=\; K_c\!\left(1 + \dfrac{O}{K_o}\right)
$$

| 記号 | 既定値 (25 °C) | 意味 |
|---|---|---|
| $V_\text{cmax,vol}$ | $1.0~\mathrm{mol\,m^{-3}\,s^{-1}}$ | 葉緑体領域の単位体積あたり Rubisco 容量 |
| $K_c$ | $27.238~\mathrm{Pa}$ | Rubisco の CO₂ Michaelis 定数 |
| $K_o$ | $16{,}582~\mathrm{Pa}$ | Rubisco の O₂ Michaelis 定数 |
| $O$ | $21{,}000~\mathrm{Pa}$ | 大気 O₂ 分圧 (標準大気) |
| $\Gamma^*$ | $3.743~\mathrm{Pa}$ | 暗呼吸非含 CO₂ 補償点 |

すべて分圧 (Pa) で扱い、状態変数 $C$ も分圧なので単位変換が不要です。
ユーザーは葉温・種特性に応じて API リクエストで上書きできます。

### Picard 線形化

$R(C)$ が $C$ について非線形なので、各反復で前回値 $C^{(k-1)}$ の
まわりで線形化:

$$
R(C) \;\approx\; R(C^{(k-1)}) \;+\; R'(C^{(k-1)})\,(C - C^{(k-1)})
\;=\; a^{(k-1)} \;+\; b^{(k-1)}\,C
$$

$$
b \;=\; R'(C) \;=\; V_\text{cmax,vol}\;\frac{K_\text{eff} + \Gamma^*}{(C + K_\text{eff})^2},
\qquad
a \;=\; R(C^{(k-1)}) - b\,C^{(k-1)}
$$

これを離散有限体積系に組み込み、反復:

$$
\Bigl[\,L \;-\; b^{(k-1)}\,\Delta x^{2}\,\mathrm{diag}\Bigr]\,C^{(k)}
\;=\; \mathbf{f}_\text{Dirichlet} \;+\; a^{(k-1)}\,\Delta x^{2}
$$

ここで $L$ は調和平均面係数で組んだ Laplacian 行列。
$R(C)$ が $C \ge \Gamma^*$ で単調増加であることが反復写像の挙動を
比較的素直にします（厳密な contractivity は楕円逆作用素 + 局所
線形化の合成に依存し、$V_\text{cmax,vol}$ や拡散係数比に依存する
評価が必要なため、本実装はそこまで証明していません）。実際には
代表的な C3/C4 入力で **5〜15 反復** で `picard_tol_pa = 10^{-4}` Pa
に収束、上限 `picard_max_iter = 50` に達した場合は `notes` に警告を
残してそのまま結果を返す graceful 動作にしています。

### 線形モード（後方互換）

`kinetics_mode="linear"` を指定すると PR #13a 互換の
$R(C) = r\,C$ で 1 回の線形 solve に切り替わります。
速度論定数の校正をスキップしたい合成テストや、感度解析で
M-M の影響を切り出すために残しています。

## 離散化 — 2D セル中心 FV

```mermaid
flowchart LR
    A[co2_morphometrics<br/>chloroplast マスク] --> SINK[sink マスク]
    B[SegFormer マスク] --> D[D(x) フィールド]
    B --> O[Ω_leaf]
    B --> BC[気孔 Dirichlet]
    D --> L[Laplacian L<br/>調和平均面係数]
    SINK --> RXN[反応 R(C^k)]
    RXN -->|線形化 a, b| ITER{Picard 反復<br/>k = 1, 2, ...}
    L --> ITER
    BC --> ITER
    ITER -->|収束| C[Cc field]
    C --> M1[cc_mean_pa]
    C --> M2[drawdown<br/>Ci − Cc]
    C --> M3[A_net]
    C --> M4[g_m_proxy]
```

葉緑体マスクは `co2_morphometrics` の overlay PNG から取り、
無い場合は `palisade` + `spongy` の合成 mesophyll マスクで代用します
(`sink_class = "mesophyll_cells"`)。

## A_net と g_m proxy

定常状態では発散定理から **3 つの量が一致** します:

$$
\underbrace{\int_{\partial \Omega_\text{stomata}} D\,\nabla C \cdot \mathbf{n}\, dl}_{\text{stomata\_supply}}
\;=\; \underbrace{\int_{\partial \Omega_\text{sink}} D\,\nabla C \cdot \mathbf{n}\, dl}_{a\_net}
\;=\; \int_{\Omega_\text{sink}} R(C)\,d\Omega
$$

実装はこのうち **sink 境界面の符号付き法線フラックス** を A_net として
報告 (`a_net = -_boundary_outflow(sink_interior)`、`pipeline/co2_diffusion.py:718`)。
M-M モードで $R(C)$ が局所的に負になる領域があっても、boundary flux
形式は自動でその寄与を取り込むため、`A_net` は **正味の Rubisco
カルボキシレーション速度** (mol s⁻¹ m⁻¹_depth) として一貫した
意味を持ちます。

Cc 平均は sink 領域内の濃度平均:

$$
C_c \;=\; \langle C \rangle_{\Omega_\text{sink}}
$$

葉長で正規化することで標準的な mesophyll conductance の単位に:

$$
g_m^\text{proxy} \;=\; \frac{A_\text{net}}{L_\text{leaf}\,(C_i - C_c)}
\quad
\bigl[\mathrm{mol\,m^{-2}\,s^{-1}\,Pa^{-1}}\bigr]
$$

$L_\text{leaf}$ は葉断面の minAreaRect 長軸（`leaf_section_length_m`）。

質量保存クロスチェック: 上の発散定理から
$\text{stomata\_supply} = \int_{\Omega_\text{sink}} R(C)\,d\Omega$ が成り立つはず。
実装は **5 % 以上の不一致** を `notes` に警告として記録します
（Picard 未収束 / 葉緑体マスクが消費組織を覆っていない / 気液界面で
の調和平均離散化誤差が大きい、などの兆候）。
PR #19 で線形モードから引き継いだ「`A_net + ∫R` で比較する double-counting バグ」
も同時に修正済み。

## 収束診断

| フィールド | 意味 |
|---|---|
| `picard_iterations` | 実際の反復回数（既定上限 50） |
| `picard_residual_pa` | 最終反復の `max\|C^k − C^{k-1}\|` |
| `notes` | 上限到達警告 / 質量不一致 / 非有限値クリップ等の文字列リスト |

`linear` モードのときは `picard_iterations = 0` で報告されます。

## API

```http
POST /images/{image_id}/analyze/co2-diffusion
Authorization: Bearer <supabase-jwt>
Content-Type: application/json

{
  "max_side_px": 1024,
  "ci_pa": 25.0,
  "kinetics_mode": "michaelis_menten",
  "vcmax_per_volume_mol_m3_s": 1.0,
  "kc_pa": 27.238,
  "ko_pa": 16582.0,
  "o2_pa": 21000.0,
  "gamma_star_pa": 3.743,
  "picard_max_iter": 50,
  "picard_tol_pa": 1e-4,
  "diffusivity": null
}
```

`reaction_rate` は `kinetics_mode="linear"` のときのみ参照されます。

## レスポンス

```jsonc
{
  "kind": "co2_diffusion",
  "result": {
    "sink_class": "chloroplast",   // または "mesophyll_cells" (フォールバック)
    "ci_pa": 25.0,
    "cc_mean_pa": 17.8,
    "drawdown_mean_pa": 7.2,
    "drawdown_max_pa": 12.1,
    "a_net": 1.24e-8,
    "leaf_section_length_m": 8.0e-4,
    "g_m_proxy": 0.21,
    "kinetics_mode": "michaelis_menten",
    "vcmax_per_volume_mol_m3_s": 1.0,
    "kc_pa": 27.238,
    "ko_pa": 16582.0,
    "o2_pa": 21000.0,
    "gamma_star_pa": 3.743,
    "picard_iterations": 7,
    "picard_residual_pa": 4.2e-5,
    "stomata_drawdowns": [
      {
        "centroid": [123.4, 56.7],
        "cc_mean_pa": 19.5,
        "drawdown_pa": 5.5,
        "flow_in": 1.2e-9
      }
      // ...
    ],
    "concentration_png_base64": "iVBORw0KGg...",
    "drawdown_png_base64": "iVBORw0KGg...",
    "heatmap_shape": [400, 800],
    "downsample_factor": 0.5,
    "diffusivity": { /* echoed back */ },
    "reaction_rate": 1.0,           // 参照されないが echo する
    "notes": []
  }
}
```

## V_cmax_volumetric の選び方

文献値の $V_\text{cmax}^\text{area}$ (µmol m⁻² s⁻¹) から本モデルの
$V_\text{cmax,vol}$ (mol m⁻³ s⁻¹) への換算は

$$
V_\text{cmax,vol} \;\approx\;
\frac{V_\text{cmax}^\text{area}\,[\mu\mathrm{mol\,m^{-2}\,s^{-1}}] \cdot 10^{-6}}
{\text{chloroplast layer thickness}\,[\mathrm{m}]}
$$

代表的なケース:

| 種別 | $V_\text{cmax}^\text{area}$ | 葉緑体層厚 | $V_\text{cmax,vol}$ |
|---|---|---|---|
| C3 平均 | 80 µmol/m²/s | 25 µm | 3.2 mol/m³/s |
| 高 V_cmax C3 (ヒマワリ) | 150 | 30 | 5.0 |
| 陰葉 / 老葉 | 30 | 20 | 1.5 |
| C4 (mesophyll Rubisco 不在のため bundle sheath で換算) | 60 | 15 | 4.0 |

既定値 1.0 mol/m³/s は控えめな C3 平均で、**典型的な mesophyll
geometry で Cc drawdown 5〜15 Pa を出す** ようキャリブレーション済み。

## デモ

C3 サンプル（Arabidopsis, 40×）で Cc フィールドが収束していく様子を
記録した動画を `public/docs-assets/co2-field-demo.mp4` に置けば、
以下の image syntax で `<video controls>` に自動 upgrade されます。

```markdown
![CO2 field convergence demo](/docs-assets/co2-field-demo.mp4 "8x 倍速")
```

![解析対象の例（プレースホルダー）](/docs-assets/placeholder.svg "ここに Cc 収束の中間ステップ静止画を置く")

## C3 vs C4 解釈の注意

> **C4 では Cc > Ci が物理的に正しい**。bundle sheath での CCM
> (PEPC + Kranz anatomy) で CO₂ が濃縮される現象は本モデルでは
> 直接表現していないので、C4 葉で本パイプラインを走らせると Cc は
> Ci 近傍に張り付き drawdown ≈ 0 になりがちです。C4 の真の g_m を
> 議論する際は **Farquhar A-Cc fit** (`gm_fit`) 側の値を使うか、
> bundle sheath 領域に追加の Dirichlet 高 CO₂ ソースを与える
> 拡張を検討してください。

> **気孔ポリゴンの欠落**。SegFormer が `stomata` を検出し漏れると
> Dirichlet 面が消滅し系が条件不良に。走らせる前に
> `coverage[stomata]` が **少なくとも 0.2%** 以上あるか確認。

> **Picard が max_iter で打ち切られた場合**、`notes` にその旨が
> 記録されます。`picard_max_iter` を上げるか `vcmax_per_volume_mol_m3_s`
> を下げて再実行してください。
