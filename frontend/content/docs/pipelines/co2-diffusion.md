---
title: CO₂ 反応拡散 PDE
description: 葉肉内部の CO₂ 濃度場を解き、g_m の幾何プロキシと A_net 近似を算出
category: pipelines
order: 70
---

## モデル — 反応拡散 PDE

葉肉組織の定常 CO₂ 拡散を Poisson-type の反応拡散方程式で扱います。

$$
\nabla \cdot \bigl(D(\mathbf{x})\,\nabla C(\mathbf{x})\bigr)
\;-\; R(C(\mathbf{x}))
\;=\; 0
\qquad \text{in }\Omega_\text{mes}
$$

| 記号 | 意味 | 単位 |
|---|---|---|
| $C$ | CO₂ 濃度 | $\mathrm{mol\,m^{-3}}$ |
| $D$ | 組織内 CO₂ 拡散係数 | $\mathrm{m^{2}\,s^{-1}}$ |
| $R(C)$ | 局所消費（光合成） | $\mathrm{mol\,m^{-3}\,s^{-1}}$ |

境界条件：

- 気孔側 (`stoma`) — ディリクレ $C = C_i$（Ci は LI-COR から、またはデフォルト 25 Pa 相当）
- 表皮外周 — ノイマン $D \nabla C \cdot \mathbf{n} = 0$
- 葉肉外縁（非気孔） — ノイマン 0

## 消費項 $R(C)$

Farquhar-von Caemmerer-Berry に倣い、Rubisco 制約の単純化形を使用します。

$$
R(C) \;=\; \rho_\text{chl}(\mathbf{x}) \cdot V_\text{cmax}\,
\frac{C}{C + K_\text{eff}}
$$

$K_\text{eff}$ は Rubisco 速度論の有効定数（$K_c$, $K_o$, $O$ から合成）。
$\rho_\text{chl}$ は CO₂ morphometrics の `chloroplasts.coverage` をマップ。

## 離散化と解法

Darcy と同じく 2D cell-centered FV。調和平均で面透過係数を作り、
反応項は後退オイラー（または Picard 反復）で解きます。

```mermaid
flowchart LR
    A[co2_morphometrics] --> D[D(x), ρ_chl(x)]
    B[SegFormer] --> O[Ω_mes + 境界]
    D --> ASM[FV + 反応 Picard]
    O --> ASM
    ASM --> C[Cc field]
    C --> M1[cc_mean_pa]
    C --> M2[drawdown<br/>Ci − Cc]
    C --> M3[A_net 近似]
    C --> M4[g_m_proxy]
```

## g_m プロキシの出し方

面積平均 $\bar C = \langle C \rangle_{\Omega_\text{mes}}$ を取り、ドロップ
$\Delta C = C_i - \bar C$ と全消費（積分された $R$）から

$$
A_\text{net} \;=\; \int_{\Omega_\text{mes}} R(C)\,d\Omega,
\qquad
g_m^\text{proxy} \;=\; \frac{A_\text{net}}{\Delta C}
$$

単位を $\mathrm{mol\,m^{-2}\,s^{-1}\,Pa^{-1}}$ に合わせるため、
境界長さ（sink_interior）で規格化します。

## 出力

```jsonc
{
  "kind": "co2_diffusion",
  "result": {
    "ci_pa": 25.0,
    "cc_mean_pa": 17.8,
    "drawdown_mean_pa": 7.2,
    "a_net": 1.24e-8,
    "g_m_proxy": 0.28,
    "concentration_png_base64": "iVBORw0KGg...",
    "reaction_png_base64":      "iVBORw0KGg..."
  }
}
```

## デモ

C3 サンプル（Arabidopsis, 40×）で Cc フィールドが収束していく
様子を記録した動画を `public/docs-assets/co2-field-demo.mp4`
に置けば、以下のような image syntax で `<video controls>` に
自動 upgrade されます。

```markdown
![CO2 field convergence demo](/docs-assets/co2-field-demo.mp4 "8x 倍速")
```

![解析対象の例（プレースホルダー）](/docs-assets/placeholder.svg "ここに Cc 収束の中間ステップ静止画を置く")

## よくある落とし穴

> **C4 では Cc > Ci**。CCM（PEPC による濃縮）を直接モデル化していないため、
> C4 葉で本モデルを走らせると drawdown が負にならない構造になっています。
> C4 の g_m を語る際は、 次章の **Farquhar A-Cc fit** 側の値を使ってください。

> **境界ポリゴンの欠け**。SegFormer が stoma を検出し漏れると
> Dirichlet 面が減って解が不安定化します。走らせる前に
> Cov の `stoma` が少なくとも **0.2%** 以上あるか確認。
