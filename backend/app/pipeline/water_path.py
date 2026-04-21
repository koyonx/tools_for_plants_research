"""Water-transport analysis: Fast Marching from xylem vessels to stomata.

Inputs come from a previously-run SegFormer analysis (`segformer_tissue`):
- Source mask: union of `xylem_vessel` polygons; falls back to `xylem`
  if vessel-level annotations weren't available.
- Sink mask: union of `stomata` polygons.
- Cost field: per-pixel water-flow resistance derived from class labels.

Outputs:
- Per-stomatum travel time + nearest source distance
- Heatmap PNG (base64) of travel time across the whole leaf
- Summary stats (min / mean / max travel time, count)

Implementation notes:
- We use scikit-fmm for the Fast Marching solve.  It's CPU-only, ~1-3 s
  on a 1024 px image, no GPU needed.
- All heavy imports (skfmm, scipy.ndimage) are deferred so the rest of
  the backend keeps booting on machines without the `ml` extra.
- Distances are reported in micrometres when an `um_per_px` is supplied;
  pixel units otherwise.  Travel time is unitless (resistance units).
"""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass, field
from typing import Any, cast

import cv2
import numpy as np

# Per-class water-flow resistance.  Values are heuristic and tuned so
# that:
# - vessel/xylem regions are essentially free (water moves easily)
# - parenchyma and bundle sheath are the typical paths
# - epidermis is a barrier (water has to leave via stomata)
# - stomata themselves are the explicit exits
# - intercellular space is intermediate (mostly vapour-phase transit)
# Operators can override via the request body.  Keys must be in
# tissue-classes.ts.  Missing classes default to BACKGROUND_COST.
DEFAULT_RESISTANCE: dict[str, float] = {
    "xylem_vessel": 0.05,
    "xylem": 0.1,
    "phloem": 4.0,
    "bundle_sheath": 1.5,
    "palisade": 1.0,
    "spongy": 1.2,
    "intercellular": 0.6,
    "stomata": 0.2,
    "upper_epidermis": 8.0,
    "lower_epidermis": 8.0,
    "other": 5.0,
}
BACKGROUND_COST = 100.0  # outside the leaf — effectively impassable


@dataclass(frozen=True)
class StomatumPath:
    centroid: list[float]
    travel_time: float
    travel_time_um: float | None
    straight_line_um: float | None
    nearest_source: list[float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WaterPathResult:
    source_class: str  # "xylem_vessel" | "xylem"
    sink_count: int
    travel_time_min: float
    travel_time_mean: float
    travel_time_max: float
    travel_time_p50: float
    paths: list[StomatumPath] = field(default_factory=list)
    heatmap_png_base64: str = ""
    heatmap_shape: tuple[int, int] = (0, 0)  # (h, w) of the heatmap PNG
    downsample_factor: float = 1.0
    resistance: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _scale_polygon(polygon: list[list[float]], factor: float) -> np.ndarray:
    pts = np.asarray(polygon, dtype=np.float32) * factor
    return pts.round().astype(np.int32)


def _rasterise_class(
    polygons: list[dict[str, Any]],
    target_class: str,
    height: int,
    width: int,
    scale: float,
) -> np.ndarray:
    """Return a uint8 mask (255 / 0) of every polygon whose class_key
    matches `target_class`.  Out-of-image vertices are clipped."""
    mask = np.zeros((height, width), dtype=np.uint8)
    for poly in polygons:
        if poly.get("class_key") != target_class:
            continue
        coords = poly.get("polygon")
        if not isinstance(coords, list) or len(coords) < 3:
            continue
        pts = _scale_polygon(coords, scale)
        if pts.ndim != 2 or pts.shape[1] != 2:
            continue
        pts[:, 0] = np.clip(pts[:, 0], 0, width - 1)
        pts[:, 1] = np.clip(pts[:, 1], 0, height - 1)
        cv2.fillPoly(mask, [pts.reshape(-1, 1, 2)], color=(255,))
    return mask


def _heatmap_to_png_base64(values: np.ndarray) -> str:
    """Render a finite ndarray to a base64-encoded magma-style PNG."""
    finite = np.isfinite(values)
    if not finite.any():
        return ""
    vmin = float(values[finite].min())
    vmax = float(values[finite].max())
    span = max(vmax - vmin, 1e-6)
    norm = np.zeros_like(values, dtype=np.float32)
    norm[finite] = (values[finite] - vmin) / span
    norm[~finite] = 0.0

    # Manual magma-ish gradient: black -> purple -> red -> orange -> yellow.
    # Avoids dragging in matplotlib for a single colormap.
    stops = np.array(
        [
            [0, 0, 0],
            [54, 16, 95],
            [137, 39, 121],
            [219, 64, 110],
            [248, 142, 96],
            [253, 231, 138],
        ],
        dtype=np.float32,
    )
    n_stops = stops.shape[0] - 1
    pos = np.clip(norm, 0.0, 1.0) * n_stops
    lo = np.floor(pos).astype(np.int32)
    hi = np.minimum(lo + 1, n_stops)
    t = (pos - lo)[..., None]
    rgb = stops[lo] * (1 - t) + stops[hi] * t
    rgba = np.zeros((*values.shape, 4), dtype=np.uint8)
    rgba[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    # Alpha 0 outside finite region so the original image shows through
    rgba[..., 3] = np.where(finite, 200, 0).astype(np.uint8)
    # cv2 expects BGRA for PNG encoding
    bgra = rgba[..., [2, 1, 0, 3]]
    ok, buf = cv2.imencode(".png", bgra)
    if not ok:
        return ""
    return base64.b64encode(bytes(buf.tobytes())).decode("ascii")


def compute_water_path(
    segformer_result: dict[str, Any],
    *,
    um_per_px: float | None = None,
    max_side_px: int = 1024,
    resistance_override: dict[str, float] | None = None,
) -> WaterPathResult:
    """Run FMM from xylem-vessel sources to stomata sinks.

    Parameters
    ----------
    segformer_result
        The `result` blob from a `segformer_tissue` analysis row.
        Must contain `polygons`, `image_shape`, `coverage`.
    um_per_px
        Optional pixel→micrometre conversion (from a basic_measurement
        scale or from images.scale_um_per_px).
    max_side_px
        Down-sample the working grid so FMM stays fast on large images.
    resistance_override
        Per-class multipliers to merge into DEFAULT_RESISTANCE.
    """
    if not isinstance(segformer_result, dict):
        raise ValueError("segformer_result must be the SegFormer result blob")

    polygons = cast(list[dict[str, Any]], segformer_result.get("polygons") or [])
    shape = segformer_result.get("image_shape") or {}
    h_orig = int(shape.get("height_px") or 0)
    w_orig = int(shape.get("width_px") or 0)
    if h_orig <= 0 or w_orig <= 0:
        raise ValueError("segformer_result lacks usable image_shape")

    longest = max(h_orig, w_orig)
    factor = max_side_px / longest if longest > max_side_px else 1.0
    h = max(int(h_orig * factor), 1)
    w = max(int(w_orig * factor), 1)
    inv_factor = 1.0 / factor

    resistance: dict[str, float] = {**DEFAULT_RESISTANCE, **(resistance_override or {})}

    # Decide source class: prefer vessel-level annotation when present.
    has_vessel = any(p.get("class_key") == "xylem_vessel" for p in polygons)
    source_class = "xylem_vessel" if has_vessel else "xylem"
    source_mask = _rasterise_class(polygons, source_class, h, w, factor)
    sink_mask = _rasterise_class(polygons, "stomata", h, w, factor)

    if source_mask.max() == 0:
        raise ValueError(
            "no xylem / xylem_vessel polygons in the SegFormer result; "
            "annotate at least one and re-run SegFormer first"
        )
    if sink_mask.max() == 0:
        raise ValueError("no stomata polygons in the SegFormer result")

    # Build the cost field.  Anywhere covered by a class polygon takes that
    # class's resistance; anywhere else gets BACKGROUND_COST so FMM sees
    # an effective wall.
    cost = np.full((h, w), BACKGROUND_COST, dtype=np.float64)
    # Paint classes in CLASS_INDEX order so later classes (vessel, sinks)
    # overwrite broader regions (palisade, spongy) underneath them.
    paint_order = [
        "palisade",
        "spongy",
        "intercellular",
        "bundle_sheath",
        "phloem",
        "xylem",
        "xylem_vessel",
        "upper_epidermis",
        "lower_epidermis",
        "stomata",
        "other",
    ]
    for cls_key in paint_order:
        cls_mask = _rasterise_class(polygons, cls_key, h, w, factor)
        if cls_mask.max() == 0:
            continue
        cost = np.where(cls_mask > 0, resistance.get(cls_key, BACKGROUND_COST), cost)

    # Sources are at zero level; everything else is +1.  scikit-fmm computes
    # arrival time by integrating the cost (slowness) along the steepest
    # descent of the level set.
    import skfmm

    phi = np.ones_like(cost, dtype=np.float64)
    phi[source_mask > 0] = -1.0
    travel_time = skfmm.travel_time(phi, speed=1.0 / np.maximum(cost, 1e-3))

    # Stomata centroids → travel time samples.  Use the original-grid
    # centroids, then convert to the down-sampled grid.
    sink_polygons = [
        p for p in polygons if p.get("class_key") == "stomata"
    ]
    paths: list[StomatumPath] = []
    arrival_finite = np.where(np.isfinite(travel_time), travel_time, np.nan)
    for poly in sink_polygons:
        coords = poly.get("polygon")
        if not isinstance(coords, list) or len(coords) < 3:
            continue
        arr = np.asarray(coords, dtype=np.float64)
        cx_orig = float(arr[:, 0].mean())
        cy_orig = float(arr[:, 1].mean())
        cx_ds = round(cx_orig * factor)
        cy_ds = round(cy_orig * factor)
        cx_ds = min(max(cx_ds, 0), w - 1)
        cy_ds = min(max(cy_ds, 0), h - 1)
        tt = float(arrival_finite[cy_ds, cx_ds])
        if not np.isfinite(tt):
            continue
        # nearest source (straight-line) for comparison
        src_pixels = np.argwhere(source_mask > 0)  # (y, x) pairs
        if src_pixels.size == 0:
            continue
        diffs = src_pixels.astype(np.float64) - np.array([cy_ds, cx_ds])
        idx = int(np.argmin(np.sum(diffs * diffs, axis=1)))
        nearest_y, nearest_x = src_pixels[idx]
        nearest_orig = [float(nearest_x) * inv_factor, float(nearest_y) * inv_factor]
        straight_um = (
            float(np.linalg.norm([nearest_x - cx_ds, nearest_y - cy_ds]))
            * inv_factor
            * (um_per_px if um_per_px else 1.0)
        ) if um_per_px else None
        paths.append(
            StomatumPath(
                centroid=[cx_orig, cy_orig],
                travel_time=tt,
                travel_time_um=tt * inv_factor * um_per_px if um_per_px else None,
                straight_line_um=straight_um,
                nearest_source=nearest_orig,
            )
        )

    if not paths:
        raise ValueError("FMM yielded no finite travel times for any stomata")

    tts = np.array([p.travel_time for p in paths], dtype=np.float64)
    heatmap_b64 = _heatmap_to_png_base64(arrival_finite)

    return WaterPathResult(
        source_class=source_class,
        sink_count=len(paths),
        travel_time_min=float(tts.min()),
        travel_time_mean=float(tts.mean()),
        travel_time_max=float(tts.max()),
        travel_time_p50=float(np.median(tts)),
        paths=paths,
        heatmap_png_base64=heatmap_b64,
        heatmap_shape=(h, w),
        downsample_factor=float(factor),
        resistance=resistance,
    )
