---
title: Darcy 水流 PDE
description: 2D 有限体積ソルバーで葉組織の水力場 P・速度場 q・葉水力コンダクタンス K_leaf を計算
category: pipelines
order: 50
---

## 物理モデル

葉組織を多孔質媒体とみなし、**Darcy の法則** を 2D 定常で解きます。

$$
\mathbf{q}(\mathbf{x}) \;=\; -\frac{k(\mathbf{x})}{\mu_w}\,\nabla P(\mathbf{x})
$$

ここで

- $\mathbf{q}$ — 体積比流量（Darcy flux）$[\,\mathrm{m^{3}\,m^{-2}\,s^{-1}} = \mathrm{m\,s^{-1}}\,]$
- $k$ — 組織の透過率 $[\mathrm{m}^2]$（組織クラスごとに割当）
- $\mu_w$ — 25 °C 水の動粘度 = $8.9 \times 10^{-4}~\mathrm{Pa\cdot s}$
- $P$ — 圧力 $[\,\mathrm{Pa}\,]$

定常質量保存 $\nabla \cdot \mathbf{q} = 0$ と合わせると、$P$ について
楕円型 PDE になります:

$$
\nabla \cdot \!\left(\frac{k(\mathbf{x})}{\mu_w}\,\nabla P\right) = 0
\quad\text{in }\Omega
$$

## 境界条件

| 部位 | 条件 | 既定値 |
|---|---|---|
| 木部維管束 (`xylem_vessel`、無ければ `xylem`) | Dirichlet $P = P_\text{xylem}$ | $0~\mathrm{Pa}$ |
| 気孔 (`stomata`) | Dirichlet $P = P_\text{stomata}$ | $-1.0 \times 10^{6}~\mathrm{Pa}$ |
| 葉外周（背景） | 物理的には no-flow（Neumann 0）。背景画素の $k$ を $\varepsilon = 10^{-18}~\mathrm{m^2}$ に固定することで自動的に実現 |

符号規約は **木部 (高水ポテンシャル) → 気孔 (低水ポテンシャル)** の方向。
$|\Delta P| = 1~\mathrm{MPa}$ は中程度の蒸散下の典型的葉肉勾配。

## 離散化 — 有限体積

セル中心圧力 $P_i$ に対し、セル面 $f$ 上の伝導率を **調和平均** で評価:

$$
K_f \;=\; \frac{2\, K_i\, K_j}{K_i + K_j},
\qquad
K \equiv \frac{k}{\mu_w}
$$

面 $f$ の体積流量 (per unit depth):

$$
q_f \;=\; -K_f\,\frac{P_j - P_i}{\Delta x}\, L_f
$$

各セルでフラックス収支を取って線形系 $A\,\mathbf{P} = \mathbf{b}$、
`scipy.sparse` の CSR + `spsolve` で解きます。

## 質量流出と K_leaf

積分された境界フラックスは **体積流量 (m²/s per metre depth)**。
これに液体水の密度 $\rho_w = 997~\mathrm{kg/m^3}$ をかけて**質量流量**
にしてから報告:

$$
\dot M_\text{out} \;=\; \rho_w \cdot \!\!\int_{\partial\Omega_\text{stomata}}\!\!\!\!\!\!
   q_f \cdot \mathbf{n}\, dl
\qquad
\bigl[\mathrm{kg\,s^{-1}\,m^{-1}}_\text{depth}\bigr]
$$

$$
K_\text{leaf} \;=\; \frac{\dot M_\text{out}}{|\Delta P|}
\qquad
\bigl[\mathrm{kg\,s^{-1}\,Pa^{-1}\,m^{-1}}_\text{depth}\bigr]
$$

> **単位履歴**: round-3 docs audit で「`flow` を kg と呼んでいるが
> 実装では密度未乗算で m²/s だった」という指摘があり、今版で
> `WATER_DENSITY_KG_M3 = 997.0` を境界積分に乗算するよう修正済み。

## 処理フロー

```mermaid
flowchart LR
    MASK[SegFormer マスク] --> K[k(x) 割り当て]
    MASK --> BC[境界条件<br/>xylem=source<br/>stomata=sink]
    K --> ASM[FV アセンブリ<br/>調和平均面係数]
    BC --> ASM
    ASM --> LIN[A·P = b<br/>CSR スパース系]
    LIN --> P[P フィールド]
    P --> Q[q ベクトル場]
    Q --> RHO[× ρ_water]
    RHO --> K_OUT[K_leaf]
    Q --> V_OUT[velocity 統計]
```

## 透過率の割当（実装に同梱）

組織別の $k$ デフォルト値 (`pipeline/darcy.py::DEFAULT_PERMEABILITY`):

| クラス | $k$ (m²) | 物性 |
|---|---|---|
| `xylem_vessel` | $5.0 \times 10^{-11}$ | 導管、最高透過 |
| `xylem` | $1.0 \times 10^{-11}$ | 木部全体（vessel 区別が無いとき fallback） |
| `bundle_sheath` | $2.0 \times 10^{-14}$ | 維管束鞘 |
| `palisade` | $1.0 \times 10^{-14}$ | 柵状葉肉細胞 |
| `spongy` | $1.5 \times 10^{-14}$ | 海綿状葉肉細胞 |
| `phloem` | $1.0 \times 10^{-16}$ | 師部、水ではなく糖を運ぶ |
| `stomata` | $1.0 \times 10^{-13}$ | sink Dirichlet 用 |
| `intercellular` | $1.0 \times 10^{-16}$ | 細胞間隙（液体水としては流れない） |
| `upper_epidermis` / `lower_epidermis` | $1.0 \times 10^{-16}$ | クチクラで遮断 |
| `other` | $5.0 \times 10^{-15}$ | 既定 |
| 背景 | $1.0 \times 10^{-18}$ | 数値 ε（事実上 no-flow） |

operator は API リクエストの `permeability` フィールドで上書き可能。
非有限値・負値は `_sanitise_overrides` で silently 落とされます。

## API

```http
POST /images/{image_id}/analyze/darcy
Authorization: Bearer <supabase-jwt>
Content-Type: application/json

{
  "max_side_px": 1024,
  "p_xylem_pa": 0.0,
  "p_stomata_pa": -1.0e6,
  "permeability": null
}
```

## レスポンス

```jsonc
{
  "kind": "darcy_flow",
  "result": {
    "source_class": "xylem_vessel",      // または "xylem" (vessel 不在時)
    "sink_class": "stomata",
    "p_xylem_pa": 0.0,
    "p_stomata_pa": -1.0e6,
    "pressure_drop_pa": 1.0e6,
    "pressure_min_pa": -1.0e6,
    "pressure_max_pa": 0.0,
    "velocity_mean": 8.2e-8,
    "velocity_p95":  3.1e-7,
    "velocity_max": 4.0e-7,
    "total_flow_in":  2.4e-10,           // kg / (s · m-depth)
    "total_flow_out": 2.4e-10,           // 同上、定常で in ≈ out
    "k_leaf": 2.4e-16,                   // kg / (s · Pa · m-depth)
    "stomata_outflows": [
      {
        "centroid": [123.4, 56.7],
        "flow": 1.8e-11,                 // kg / (s · m-depth)
        "mean_velocity": 4.2e-8          // m/s
      }
      // ...
    ],
    "pressure_png_base64": "iVBORw0KGg...",
    "velocity_png_base64": "iVBORw0KGg...",
    "heatmap_shape": [400, 800],
    "downsample_factor": 0.5,
    "permeability": { /* ... */ },
    "notes": []
  }
}
```

## C3 / C4 解釈の補足

> 絶対値は `k_tissue` の経験定数に強く依存するので、**C3 コホートと
> C4 コホート間の比** を見ることを推奨します。例: 同じ `permeability`
> overrides で C4 / C3 の K_leaf 比率が有意に > 1 ならば、C4 側の
> 気道配置が駆動圧当たりの水流量を大きくする構造であることを示唆。

> 質量流量とコンダクタンスは画像が **2D 断面**であることを反映して
> 「per metre depth」の単位を持ちます。3D 葉全体の流量に変換するに
> は葉幅で乗算してください（複数枚の断面で平均することを推奨）。
