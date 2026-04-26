---
title: Darcy 水流 PDE
description: 2D 有限体積ソルバーで葉水力コンダクタンス K_leaf を計算
category: pipelines
order: 50
---

## 物理モデル

葉組織を多孔質媒体とみなし、**Darcy の法則** を 2D 定常で解きます。

$$
\mathbf{q}(\mathbf{x}) \;=\; -\frac{k(\mathbf{x})}{\mu_w}\,\nabla P(\mathbf{x})
$$

ここで

- $\mathbf{q}$ — 比流量（Darcy flux）$[\,\mathrm{kg\,m^{-2}\,s^{-1}}\,]$
- $k$ — 組織の透過率 $[\mathrm{m}^2]$（組織クラスごとに割当）
- $\mu_w$ — 水の粘度 $[\,\mathrm{Pa\cdot s}\,]$
- $P$ — 圧力 $[\,\mathrm{Pa}\,]$

定常連続の式 $\nabla \cdot (\rho \mathbf{q}) = 0$ と合わせて圧力 $P$ の楕円型 PDE になります。

$$
\nabla \cdot \!\left(\frac{k(\mathbf{x})}{\mu_w}\,\nabla P\right) = 0
\quad\text{in }\Omega
$$

境界条件:

| 部位 | 条件 |
|---|---|
| 維管束（source） | $P = P_\text{xylem}$ (Dirichlet) |
| 気孔（sink） | $P = P_\text{atm}$ (Dirichlet) |
| 表皮外周 | $\mathbf{q}\cdot\mathbf{n} = 0$ (Neumann) |

## 離散化 — 有限体積

セル中心変数 $P_i$ に対し、セル面 $f$ 上のフラックスを **調和平均透過率** で評価します。

$$
k_f \;=\; \frac{2\, k_i\, k_j}{k_i + k_j}
\quad(\text{セル } i,\,j \text{ が面 } f \text{ を共有})
$$

面 $f$ のフラックスは

$$
q_f \;=\; -\frac{k_f}{\mu_w}\,\frac{P_j - P_i}{\Delta x}\, L_f
$$

各セルでフラックス収支を取ると線形系 $A\,\mathbf{P} = \mathbf{b}$ が得られ、
scipy.sparse の CSR + `spsolve` で解きます。

## K_leaf の定義

気孔側から流出する総流量を駆動圧で割り、葉の幅で正規化して
**単位幅あたりの葉水力コンダクタンス** を計算します。

$$
K_\text{leaf} \;=\; \frac{Q_\text{out}}{\Delta P \cdot L_\text{leaf}}
\qquad
\bigl[\,\mathrm{kg\,s^{-1}\,Pa^{-1}\,m^{-1}}\,\bigr]
$$

$Q_\text{out}$ は全気孔からの流出を足し合わせた値（境界条件の Dirichlet 面から逆算）。

## 処理フロー

```mermaid
flowchart LR
    MASK[SegFormer マスク] --> K[k(x) 割り当て]
    MASK --> BC[境界条件<br/>vein=source<br/>stoma=sink]
    K --> ASM[FV アセンブリ]
    BC --> ASM
    ASM --> LIN[A·P = b<br/>CSR スパース系]
    LIN --> P[P フィールド]
    P --> Q[q フィールド]
    Q --> K_OUT[K_leaf]
    Q --> V_OUT[velocity 統計]
```

## 出力

```jsonc
{
  "kind": "darcy_flow",
  "result": {
    "k_leaf": 4.8e-13,
    "pressure_drop_pa": 0.5e6,
    "velocity_mean": 8.2e-8,
    "velocity_p95":  3.1e-7,
    "total_flow_out": 2.4e-13,
    "stomata_outflows": [
      { "stoma_id": 1, "flux_kg_s_m": 1.8e-14 },
      /* ... */
    ],
    "pressure_png_base64": "iVBORw0KGg...",
    "velocity_png_base64": "iVBORw0KGg..."
  }
}
```

## 透過率の割当

経験的初期値を `app/pipeline/darcy.py::TISSUE_PERMEABILITY_M2` で定義しています。

```python
TISSUE_PERMEABILITY_M2 = {
    "mesophyll":     5.0e-13,
    "bundle_sheath": 3.0e-13,
    "vein":          1.0e-10,   # 維管束内は高コンダクタンス
    "epidermis":     1.0e-16,   # ほぼ遮断
    "air_space":     2.0e-12,
    "stoma":         1.0e-11,
}
```

個別の画像について調整する場合は、UI の <kbd>permeability overrides</kbd> から
JSON を上書き送信できます。

## 補足：C3 / C4 解釈

> 絶対値は `k_tissue` の経験定数に強く依存するので、
> **C3 コホートと C4 コホート間の比** を見ることを推奨します。
> 例：同じ overrides で C4 / C3 の K_leaf 比率が有意に > 1 ならば、
> C4 側の気道配置が駆動圧当たりの水流量を大きくする構造であることを示唆。
