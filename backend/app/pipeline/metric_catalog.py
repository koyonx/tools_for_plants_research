"""Pure metric catalog shared by the /compare API and the pipeline
validators.

Split out of `app.api.compare` so `app.pipeline.*` modules can
reference the metric list (label, unit, JSON path into a result
document, owning analysis_kind) without importing FastAPI.  The
compare router re-exports these names for backwards compatibility.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class MetricDef:
    key: str
    label: str
    unit: str
    analysis_kind: str
    # JSON path fragments into analyses.result.  `None` elements are
    # skipped (e.g. for array-backed keys handled elsewhere); for now all
    # metrics are plain dotted paths into nested dicts.
    path: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "path": list(self.path)}


# Scalar metrics every pipeline already exposes.  Extend here when
# adding a new pipeline — the `/compare/metrics` endpoint just returns
# this list, so the UI picks up new keys automatically.
METRICS: tuple[MetricDef, ...] = (
    MetricDef(
        key="leaf_area_um2",
        label="葉断面面積",
        unit="µm²",
        analysis_kind="basic_measurement",
        path=("measurement", "leaf_area_um2"),
    ),
    MetricDef(
        key="leaf_mean_thickness_um",
        label="葉厚 平均",
        unit="µm",
        analysis_kind="basic_measurement",
        path=("measurement", "leaf_mean_thickness_um"),
    ),
    MetricDef(
        key="leaf_median_thickness_um",
        label="葉厚 中央",
        unit="µm",
        analysis_kind="basic_measurement",
        path=("measurement", "leaf_median_thickness_um"),
    ),
    MetricDef(
        key="leaf_max_thickness_um",
        label="葉厚 最大",
        unit="µm",
        analysis_kind="basic_measurement",
        path=("measurement", "leaf_max_thickness_um"),
    ),
    MetricDef(
        key="cellpose_cell_count",
        label="Cellpose 細胞数",
        unit="個",
        analysis_kind="cellpose_cells",
        path=("cell_count",),
    ),
    MetricDef(
        key="cellpose_mean_area_px",
        label="Cellpose 細胞平均面積",
        unit="px²",
        analysis_kind="cellpose_cells",
        path=("mean_area_px",),
    ),
    MetricDef(
        key="water_travel_time_mean",
        label="水経路 平均 travel time",
        unit="µm·cost",
        analysis_kind="water_path",
        path=("travel_time_mean",),
    ),
    MetricDef(
        key="water_travel_time_p50",
        label="水経路 中央 travel time",
        unit="µm·cost",
        analysis_kind="water_path",
        path=("travel_time_p50",),
    ),
    MetricDef(
        key="water_sink_count",
        label="気孔数 (water_path 経由)",
        unit="個",
        analysis_kind="water_path",
        path=("sink_count",),
    ),
    # CO2 diffusion morphometrics (Evans & von Caemmerer / Tosens et al.
    # 2D cross-section proxies).  S_mes/S and S_c/S are dimensionless —
    # the 2D definitions divide cell/chloroplast perimeter sum by leaf
    # section length, so the ratio is comparable across species even
    # without a ground-truth um/px scale bar.
    MetricDef(
        key="co2_s_mes_s",
        label="S_mes/S (葉肉細胞露出面/葉面)",
        unit="-",
        analysis_kind="co2_morphometrics",
        path=("s_mes_s",),
    ),
    MetricDef(
        key="co2_s_c_s",
        label="S_c/S (葉緑体露出面/葉面)",
        unit="-",
        analysis_kind="co2_morphometrics",
        path=("s_c_s",),
    ),
    MetricDef(
        key="co2_f_ias",
        label="f_ias (細胞間隙率)",
        unit="-",
        analysis_kind="co2_morphometrics",
        path=("f_ias",),
    ),
    MetricDef(
        key="co2_t_cw_median_um",
        label="T_cw 中央 (細胞壁厚 proxy)",
        unit="µm",
        analysis_kind="co2_morphometrics",
        path=("cell_wall", "t_cw_median_um"),
    ),
    MetricDef(
        key="co2_t_cw_p95_um",
        label="T_cw 95%tile (細胞壁厚 proxy)",
        unit="µm",
        analysis_kind="co2_morphometrics",
        path=("cell_wall", "t_cw_p95_um"),
    ),
    MetricDef(
        key="co2_chloroplast_count",
        label="葉緑体数",
        unit="個",
        analysis_kind="co2_morphometrics",
        path=("chloroplasts", "count"),
    ),
    MetricDef(
        key="co2_chloroplast_coverage",
        label="葉緑体 / 葉肉細胞面積比",
        unit="-",
        analysis_kind="co2_morphometrics",
        path=("chloroplasts", "coverage_of_mesophyll_cells"),
    ),
    MetricDef(
        key="co2_mesophyll_thickness_median_um",
        label="葉肉層厚 中央",
        unit="µm",
        analysis_kind="co2_morphometrics",
        path=("mesophyll", "thickness_median_um"),
    ),
    # Darcy water-flow scalars (PR #12).  K_leaf in particular is
    # the headline number: a physical hydraulic conductance that
    # should differ measurably between C3 and C4 cohorts driven by
    # mesophyll geometry differences, providing a complement to the
    # morphology-only S_mes/S / f_ias metrics above.
    MetricDef(
        key="darcy_k_leaf",
        label="K_leaf (葉水力コンダクタンス)",
        unit="kg/(s·Pa·m)",
        analysis_kind="darcy_flow",
        path=("k_leaf",),
    ),
    MetricDef(
        key="darcy_mean_velocity",
        label="平均流速",
        unit="m/s",
        analysis_kind="darcy_flow",
        path=("velocity_mean",),
    ),
    MetricDef(
        key="darcy_p95_velocity",
        label="流速 95%tile",
        unit="m/s",
        analysis_kind="darcy_flow",
        path=("velocity_p95",),
    ),
    MetricDef(
        key="darcy_total_flow_out",
        label="総流出量 (気孔側)",
        unit="kg/(s·m)",
        analysis_kind="darcy_flow",
        path=("total_flow_out",),
    ),
    MetricDef(
        key="darcy_pressure_drop_pa",
        label="圧力差 ΔP",
        unit="Pa",
        analysis_kind="darcy_flow",
        path=("pressure_drop_pa",),
    ),
    # CO2 reaction-diffusion scalars (PR #13a).  g_m_proxy is the
    # headline C3/C4 comparison number — the full Farquhar A-Cc fit
    # against LI-COR data lands in PR #13b.
    MetricDef(
        key="co2_g_m_proxy",
        label="g_m (葉肉コンダクタンス近似)",
        unit="mol/(m²·s·Pa)",
        analysis_kind="co2_diffusion",
        path=("g_m_proxy",),
    ),
    MetricDef(
        key="co2_cc_mean_pa",
        label="Cc 平均 (葉緑体 CO2)",
        unit="Pa",
        analysis_kind="co2_diffusion",
        path=("cc_mean_pa",),
    ),
    MetricDef(
        key="co2_drawdown_mean_pa",
        label="CO2 降下 平均",
        unit="Pa",
        analysis_kind="co2_diffusion",
        path=("drawdown_mean_pa",),
    ),
    MetricDef(
        key="co2_a_net",
        label="A_net (同化速度近似)",
        unit="mol/(s·m)",
        analysis_kind="co2_diffusion",
        path=("a_net",),
    ),
)
METRICS_BY_KEY: dict[str, MetricDef] = {m.key: m for m in METRICS}
