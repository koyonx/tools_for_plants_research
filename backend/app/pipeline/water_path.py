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
    # First xylem source pixel reached by the gradient-descent trace, in
    # original-image coordinates.
    nearest_source: list[float]
    # Polyline traced down the gradient of the FMM travel-time field, in
    # original-image coordinates ([[x, y], ...]).  Always at least
    # `[centroid, nearest_source]`; for tissue maps with detours this
    # carries the actual minimum-resistance route.
    route: list[list[float]] = field(default_factory=list)
    # True when the gradient trace plateaued / hit the step budget and
    # the final segment was snapped to the Euclidean-nearest source
    # pixel (which may cross high-cost barriers).  UI dashes that
    # segment so users don't read the snap as a real route.
    truncated: bool = False

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
    matches `target_class`.  Out-of-image vertices are clipped.  Polygon
    `holes` (PR #5c) are punched back out so a bundle-sheath ring around
    xylem doesn't cover the inner xylem in the cost field."""
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
        # Punch holes back out so this class doesn't claim pixels that
        # actually belong to the enclosed class.
        for hole in poly.get("holes") or []:
            if not isinstance(hole, list) or len(hole) < 3:
                continue
            hpts = _scale_polygon(hole, scale)
            if hpts.ndim != 2 or hpts.shape[1] != 2:
                continue
            hpts[:, 0] = np.clip(hpts[:, 0], 0, width - 1)
            hpts[:, 1] = np.clip(hpts[:, 1], 0, height - 1)
            cv2.fillPoly(mask, [hpts.reshape(-1, 1, 2)], color=(0,))
    return mask


def _heatmap_to_png_base64(values: np.ndarray, alpha_mask: np.ndarray | None = None) -> str:
    """Render a finite ndarray to a base64-encoded magma-style PNG.

    `alpha_mask` (uint8 or bool, same shape as `values`): pixels where
    this is zero get alpha=0 in the output PNG.  When omitted, alpha
    follows the finiteness of `values` — fine for fields that are
    truly infinite outside the leaf, but use the explicit mask when
    FMM happens to produce finite numbers across BACKGROUND_COST
    regions (which it does once `dx` rescaling is applied)."""
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
    if alpha_mask is None:
        rgba[..., 3] = np.where(finite, 200, 0).astype(np.uint8)
    else:
        rgba[..., 3] = np.where((alpha_mask > 0) & finite, 200, 0).astype(np.uint8)
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

    # Sanitise the user-supplied override.  Pydantic accepts NaN / +Inf /
    # negatives for an unconstrained `dict[str, float]`, and any of those
    # in the cost field would either crash skfmm or silently poison the
    # arrival-time grid.  Keep only finite, strictly-positive values.
    sanitised_override: dict[str, float] = {}
    for k, v in (resistance_override or {}).items():
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv > 0 and fv < 1e6:
            sanitised_override[k] = fv
    resistance: dict[str, float] = {**DEFAULT_RESISTANCE, **sanitised_override}

    # Decide source class: prefer vessel-level annotation when present.
    has_vessel = any(p.get("class_key") == "xylem_vessel" for p in polygons)
    source_class = "xylem_vessel" if has_vessel else "xylem"
    source_mask = _rasterise_class(polygons, source_class, h, w, factor)
    # Stomata mask is used only as a presence guard here — the FMM solve
    # treats stomata as ordinary pixels with their own class resistance,
    # and we sample the resulting `travel_time` field at each stomatum
    # centroid below.  Stomata are NOT terminating boundaries; arrival
    # times at non-stomata pixels can include routes that pass through
    # stomata if those happen to lie on the cheapest path.
    sink_mask_present = _rasterise_class(polygons, "stomata", h, w, factor)

    if source_mask.max() == 0:
        raise ValueError(
            "no xylem / xylem_vessel polygons in the SegFormer result; "
            "annotate at least one and re-run SegFormer first"
        )
    if sink_mask_present.max() == 0:
        raise ValueError("no stomata polygons in the SegFormer result")

    # Build the cost field.  Anywhere covered by a class polygon takes that
    # class's resistance; anywhere else gets BACKGROUND_COST so FMM sees
    # an effective wall.  We also accumulate a "leaf coverage" mask out of
    # the class polygons — used later to alpha-mask the heatmap so the
    # overlay never tints off-leaf background.
    cost = np.full((h, w), BACKGROUND_COST, dtype=np.float64)
    leaf_mask = np.zeros((h, w), dtype=np.uint8)
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
        leaf_mask = np.maximum(leaf_mask, cls_mask)

    # Sources are at zero level; everything else is +1.  scikit-fmm computes
    # arrival time by integrating cost (slowness) along the steepest
    # descent of the level set.  Pass `dx=inv_factor` so the grid spacing
    # is in *original-image* pixel units — that way travel-time numbers
    # are independent of the chosen `max_side_px` down-sample factor.
    import skfmm

    phi = np.ones_like(cost, dtype=np.float64)
    phi[source_mask > 0] = -1.0
    speed = 1.0 / np.maximum(cost, 1e-3)
    travel_time = skfmm.travel_time(phi, speed=speed, dx=inv_factor)
    arrival_finite = np.where(np.isfinite(travel_time), travel_time, np.nan)

    # Pre-compute the gradient for back-tracing the FMM path.  The fastest
    # route from any sink to its source is the steepest descent of
    # travel_time, so we walk in the -gradient direction one pixel at a
    # time until we land on the source mask (or hit a step budget).
    grad_y, grad_x = np.gradient(np.where(np.isfinite(travel_time), travel_time, 0.0))

    sample_every = 4

    def _trace_route(
        start_y: int, start_x: int, max_steps: int = 4096
    ) -> list[tuple[float, float]]:
        """Gradient-descent polyline (downsampled-grid coords) from a
        sink centroid back to the source mask.  Samples every Nth step
        so the polyline stays compact even on long routes."""
        y, x = float(start_y), float(start_x)
        route_ds: list[tuple[float, float]] = [(y, x)]
        last_iy, last_ix = -1, -1
        for step in range(1, max_steps + 1):
            iy, ix = round(y), round(x)
            iy = min(max(iy, 0), h - 1)
            ix = min(max(ix, 0), w - 1)
            if source_mask[iy, ix] > 0:
                route_ds.append((float(iy), float(ix)))
                return route_ds
            dy = -float(grad_y[iy, ix])
            dx_ = -float(grad_x[iy, ix])
            norm = (dy * dy + dx_ * dx_) ** 0.5
            if norm < 1e-9:
                # flat plateau — bail out instead of looping forever
                break
            y += dy / norm
            x += dx_ / norm
            if not (0 <= y < h and 0 <= x < w):
                break
            # Sample every SAMPLE_EVERY steps, but always include
            # changes when we move to a new pixel cell so the polyline
            # has at least one intermediate vertex on short routes.
            if step % sample_every == 0 and (iy, ix) != (last_iy, last_ix):
                route_ds.append((y, x))
                last_iy, last_ix = iy, ix

        # Trace ended without landing on a source (plateau or budget).
        # Return whatever we have — the caller resolves a fallback
        # source endpoint via Euclidean-nearest below so `nearest_source`
        # is never the sink itself.
        return route_ds

    sink_polygons = [p for p in polygons if p.get("class_key") == "stomata"]
    paths: list[StomatumPath] = []
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

        route_ds = _trace_route(cy_ds, cx_ds)

        # If the trace bailed before reaching the source mask, snap the
        # endpoint to the Euclidean-nearest source pixel and append it
        # so `route` is always sink → … → source and `nearest_source`
        # is never the sink itself.  Mark the path `truncated=True` so
        # the UI can dash that snap segment — it may cross high-cost
        # tissue and is not a real FMM route.
        truncated = False
        end_y_int = min(max(round(route_ds[-1][0]), 0), h - 1)
        end_x_int = min(max(round(route_ds[-1][1]), 0), w - 1)
        if source_mask[end_y_int, end_x_int] == 0:
            truncated = True
            src_pixels = np.argwhere(source_mask > 0)
            if src_pixels.size > 0:
                diffs = src_pixels.astype(np.float64) - np.array(
                    [route_ds[-1][0], route_ds[-1][1]]
                )
                idx = int(np.argmin(np.sum(diffs * diffs, axis=1)))
                snap_y, snap_x = src_pixels[idx]
                route_ds.append((float(snap_y), float(snap_x)))

        # Convert back to original-image coords + (x, y) order for the UI.
        route_orig: list[list[float]] = [
            [float(rx) * inv_factor, float(ry) * inv_factor] for ry, rx in route_ds
        ]
        # Endpoint of the FMM trace = the source pixel actually reached.
        end_y, end_x = route_ds[-1]
        nearest_orig = [float(end_x) * inv_factor, float(end_y) * inv_factor]

        # Straight-line distance to the same endpoint, for comparison
        # against the FMM travel cost.
        straight_um = (
            float(np.linalg.norm([end_x - cx_ds, end_y - cy_ds]))
            * inv_factor
            * um_per_px
            if um_per_px
            else None
        )
        # tt is already in original-pixel-cost units thanks to dx=inv_factor;
        # multiply by µm/px to get a µm-cost surrogate (no longer applies
        # the down-sample factor manually).
        tt_um = tt * um_per_px if um_per_px else None

        paths.append(
            StomatumPath(
                centroid=[cx_orig, cy_orig],
                travel_time=tt,
                travel_time_um=tt_um,
                straight_line_um=straight_um,
                nearest_source=nearest_orig,
                route=route_orig,
                truncated=truncated,
            )
        )

    if not paths:
        raise ValueError("FMM yielded no finite travel times for any stomata")

    tts = np.array([p.travel_time for p in paths], dtype=np.float64)
    heatmap_b64 = _heatmap_to_png_base64(arrival_finite, alpha_mask=leaf_mask)

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
