"""CO2-diffusion morphometrics from SegFormer tissue polygons + Cellpose cells.

Computes the scalar inputs that feed the Evans & von Caemmerer /
Tosens et al. mesophyll-conductance model:

* **S_mes/S** — mesophyll cell surface area exposed to the intercellular
  air space, per unit leaf surface area.  In a 2-D cross-section the
  standard proxy is `Σ(cell perimeter) / (leaf section length)` summed
  over cells inside the mesophyll region (Thain 1983; Evans & Loreto
  2000).  Dimensionless; higher in C3 mesophytes than in C4 NADP-ME
  species with fewer exposed cells per µm leaf.
* **S_c/S** — chloroplast surface area per unit leaf length, same
  convention.  Fraction-of-a-cell metric that captures how much of the
  cell periphery actually carries chloroplasts (critical for g_m).
* **f_ias** — intercellular air space fraction within the mesophyll,
  `1 - Σ(cell area) / (mesophyll area)`.  Captures the geometric
  porosity that gas-phase CO2 traverses.
* **T_cw** — cell-wall thickness proxy from the distance transform of
  the intercellular gap region (nearest-cell distance at each gap
  pixel).  Approximate — TEM-level T_cw is out of scope here — but
  consistent across runs and sufficient for *between*-group comparison,
  which is the research question.
* Leaf-length basis (denominator in S_mes/S and S_c/S) is measured as
  the major axis of the mesophyll region's minimum-area rectangle,
  converted to µm via `um_per_px`.  That keeps the measurement
  rotation-invariant for sections cut off the perfect horizontal.

Inputs
------
Two existing analyses blobs (`segformer_tissue` and `cellpose_cells`)
plus the raw image bytes.  We don't re-run any ML here — this pipeline
is purely classical CV on top of the polygons and pixel data that
upstream pipelines already produce.

Outputs
-------
A flat JSON-safe result dict with every scalar at the top level so the
`/compare` dashboard can pick it up via a one-tuple JSON path.  Nested
sub-groups (`mesophyll`, `chloroplasts`, `cell_wall`) carry the full
detail for the image-detail panel.

The chloroplast detector is deliberately classical CV (LAB a* channel
Otsu inside each cell mask, with a size filter).  It's tunable via
parameters and easy to swap for a learned detector later without
changing the public JSON surface.
"""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass, field
from typing import Any, cast

import cv2
import numpy as np

MESOPHYLL_CLASSES: tuple[str, ...] = ("palisade", "spongy")

# Chloroplast detector defaults — classical CV pass over LAB a*.
# Tunable via `parameters.chloroplast` in the request body.
DEFAULT_CHLOROPLAST_MIN_AREA_PX = 6
DEFAULT_CHLOROPLAST_MAX_AREA_RATIO = 0.8  # of parent cell
# When LAB-a* contrast is too flat for Otsu to separate (e.g. unstained
# H&E sections), skip detection rather than emit bogus chloroplast blobs.
# Empirically, meaningful green-pigment contrast has a* range >= this
# many units (out of 0..255).
MIN_A_CHANNEL_CONTRAST = 8


@dataclass(frozen=True)
class MesophyllStats:
    area_px: int
    area_um2: float | None
    thickness_mean_um: float | None
    thickness_median_um: float | None
    section_length_um: float | None
    section_length_px: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CellAggregateStats:
    count: int
    perimeter_total_um: float | None
    perimeter_total_px: float
    area_total_um2: float | None
    area_total_px: float
    mean_perimeter_um: float | None
    mean_area_um2: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ChloroplastStats:
    count: int
    total_area_px: float
    total_area_um2: float | None
    mean_area_um2: float | None
    total_perimeter_um: float | None
    coverage_of_mesophyll_cells: float | None
    detection_method: str
    a_channel_contrast: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CellWallStats:
    # All thickness figures are in µm when `um_per_px` is supplied; px
    # otherwise.  Reported both since the scale factor is ground-truth
    # only when the operator ran a basic_measurement scale-bar first.
    # Every numeric field is nullable: when there are no mesophyll-
    # clipped cells to seed the distance transform, T_cw is undefined
    # and reported as `None` rather than a misleading zero — else
    # `/compare` would treat those zeros as real population values.
    t_cw_mean_um: float | None
    t_cw_median_um: float | None
    t_cw_p95_um: float | None
    t_cw_mean_px: float | None
    t_cw_median_px: float | None
    t_cw_p95_px: float | None
    gap_pixel_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Co2MorphometricsResult:
    source_class: tuple[str, ...]
    downsample_factor: float
    um_per_px: float | None
    image_shape: dict[str, int]

    mesophyll: MesophyllStats
    mesophyll_cells: CellAggregateStats
    chloroplasts: ChloroplastStats
    cell_wall: CellWallStats

    # Top-level scalars — flat here so compare.py METRICS can extract
    # each as a one-tuple JSON path.
    s_mes_s: float | None
    s_c_s: float | None
    f_ias: float | None

    chloroplast_overlay_png_base64: str
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_class": list(self.source_class),
            "downsample_factor": self.downsample_factor,
            "um_per_px": self.um_per_px,
            "image_shape": self.image_shape,
            "mesophyll": self.mesophyll.to_dict(),
            "mesophyll_cells": self.mesophyll_cells.to_dict(),
            "chloroplasts": self.chloroplasts.to_dict(),
            "cell_wall": self.cell_wall.to_dict(),
            "s_mes_s": self.s_mes_s,
            "s_c_s": self.s_c_s,
            "f_ias": self.f_ias,
            "chloroplast_overlay_png_base64": self.chloroplast_overlay_png_base64,
            "notes": list(self.notes),
        }


def _scale_polygon(polygon: list[list[float]], factor: float) -> np.ndarray:
    pts = np.asarray(polygon, dtype=np.float32) * factor
    return pts.round().astype(np.int32)


def _rasterise_classes(
    polygons: list[dict[str, Any]],
    target_classes: tuple[str, ...],
    height: int,
    width: int,
    scale: float,
) -> np.ndarray:
    """Union mask (255/0) of every polygon whose class_key is in `target_classes`.
    Polygon holes (PR #5c) are subtracted so a bundle-sheath ring around
    xylem doesn't cover the inner xylem region."""
    target = set(target_classes)
    mask = np.zeros((height, width), dtype=np.uint8)
    for poly in polygons:
        if poly.get("class_key") not in target:
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


def _polygon_perimeter_px(polygon: list[list[float]]) -> float:
    arr = np.asarray(polygon, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[0] < 2 or arr.shape[1] != 2:
        return 0.0
    shifted = np.roll(arr, -1, axis=0)
    return float(np.sum(np.sqrt(np.sum((shifted - arr) ** 2, axis=1))))


def _polygon_area_px(polygon: list[list[float]]) -> float:
    """Shoelace area for a polygon in (x, y) coords."""
    arr = np.asarray(polygon, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[0] < 3 or arr.shape[1] != 2:
        return 0.0
    x = arr[:, 0]
    y = arr[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) * 0.5)


def _point_in_mask(mask: np.ndarray, x: float, y: float) -> bool:
    h, w = mask.shape[:2]
    ix = round(x)
    iy = round(y)
    if ix < 0 or iy < 0 or ix >= w or iy >= h:
        return False
    return bool(mask[iy, ix] > 0)


def _mesophyll_section_length_ds_px(mask_ds: np.ndarray) -> float:
    """Major axis of the min-area rotated rectangle around the mesophyll
    mask.  Falls back to the bounding-box width if the mask is empty.
    """
    ys, xs = np.where(mask_ds > 0)
    if xs.size == 0:
        return 0.0
    pts = np.column_stack([xs, ys]).astype(np.float32)
    if pts.shape[0] < 3:
        return float(xs.max() - xs.min())
    rect = cv2.minAreaRect(pts)
    (_cx, _cy), (rw, rh), _angle = rect
    return float(max(rw, rh))


def _column_thickness_ds_px(mask_ds: np.ndarray) -> tuple[float | None, float | None]:
    """Mean/median of the count of mesophyll pixels in each image column
    that contains at least one mesophyll pixel — a cheap proxy for the
    typical vertical extent of the mesophyll layer.  Returns (None, None)
    when the mask is empty.  Down-sampled-pixel units.
    """
    col_counts = mask_ds.sum(axis=0) / 255
    nonzero = col_counts[col_counts > 0]
    if nonzero.size == 0:
        return None, None
    return float(nonzero.mean()), float(np.median(nonzero))


def _cells_inside(
    cells: list[dict[str, Any]], mask_ds: np.ndarray, scale: float
) -> list[dict[str, Any]]:
    """Filter Cellpose cells to those whose centroid lies inside
    `mask_ds` (which is in down-sampled-grid coords).  Cells missing a
    centroid are skipped (silently) — upstream Cellpose output always
    includes one for detected cells."""
    kept: list[dict[str, Any]] = []
    for cell in cells:
        centroid = cell.get("centroid")
        if not (isinstance(centroid, list) and len(centroid) == 2):
            continue
        cx = float(centroid[0]) * scale
        cy = float(centroid[1]) * scale
        if _point_in_mask(mask_ds, cx, cy):
            kept.append(cell)
    return kept


def _render_cells_mask(
    cells: list[dict[str, Any]],
    height: int,
    width: int,
    scale: float,
) -> np.ndarray:
    """Filled mask of every cell polygon in `cells`, at the down-sampled grid."""
    mask = np.zeros((height, width), dtype=np.uint8)
    for cell in cells:
        coords = cell.get("polygon")
        if not isinstance(coords, list) or len(coords) < 3:
            continue
        pts = _scale_polygon(coords, scale)
        if pts.ndim != 2 or pts.shape[1] != 2:
            continue
        pts[:, 0] = np.clip(pts[:, 0], 0, width - 1)
        pts[:, 1] = np.clip(pts[:, 1], 0, height - 1)
        cv2.fillPoly(mask, [pts.reshape(-1, 1, 2)], color=(255,))
    return mask


def _detect_chloroplasts_in_cell(
    image_lab_cell: np.ndarray,
    cell_mask: np.ndarray,
    min_area_px: int,
    max_area_ratio: float,
) -> tuple[list[np.ndarray], float]:
    """Threshold the cell's LAB a* channel (negative-a = green) with Otsu
    to separate chloroplasts from cytoplasm.  Returns the list of
    chloroplast contours (in the cell-local cropped frame) + the a*
    contrast (range of a* values inside the cell mask).
    """
    a_channel = image_lab_cell[..., 1]
    inside = a_channel[cell_mask > 0]
    if inside.size == 0:
        return [], 0.0
    contrast = float(inside.max() - inside.min())
    if contrast < MIN_A_CHANNEL_CONTRAST:
        return [], contrast
    # Chloroplasts are "more green than the cytoplasm" ⇒ lower a*.  Otsu
    # on a* finds the threshold; we keep the darker (more-green) pixels.
    # Mask out off-cell pixels so the histogram isn't polluted by
    # neighbouring tissue.
    masked_a = np.where(cell_mask > 0, a_channel, 255).astype(np.uint8)
    _, otsu = cv2.threshold(masked_a, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    otsu = cv2.bitwise_and(otsu, otsu, mask=cell_mask)
    # Morphological open to drop speckle noise (single-pixel blobs from
    # per-pixel threshold wobble).
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    otsu = cv2.morphologyEx(otsu, cv2.MORPH_OPEN, kernel)
    contours, _ = cv2.findContours(otsu, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cell_area = float(cell_mask.sum()) / 255.0
    kept: list[np.ndarray] = []
    for c in contours:
        area = float(cv2.contourArea(c))
        if area < min_area_px:
            continue
        if cell_area > 0 and area / cell_area > max_area_ratio:
            # Skip contours that cover nearly the entire cell — those
            # are almost always Otsu degenerating on a low-contrast
            # cell, not a real chloroplast.
            continue
        kept.append(c)
    return kept, contrast


def _render_overlay_png(
    image_bgr_ds: np.ndarray,
    mesophyll_mask_ds: np.ndarray,
    cells_mask_ds: np.ndarray,
    chloroplast_mask_ds: np.ndarray,
) -> str:
    """Faint dim → cell-outline green → chloroplast-filled bright green
    overlay on the down-sampled image, encoded as a base64 PNG."""
    base = image_bgr_ds.copy()
    dim = np.where(mesophyll_mask_ds[..., None] > 0, base, (base * 0.4).astype(np.uint8))
    rgba = cv2.cvtColor(dim, cv2.COLOR_BGR2BGRA)

    # Cell boundaries in translucent green.
    cell_edges = cv2.Canny(cells_mask_ds, 50, 150)
    rgba[cell_edges > 0] = (30, 200, 80, 220)

    # Chloroplast pixels in bright magenta so they pop against the cell
    # outlines even on greenish microscope fields.
    rgba[chloroplast_mask_ds > 0] = (200, 30, 200, 230)

    ok, buf = cv2.imencode(".png", rgba)
    if not ok:
        return ""
    return base64.b64encode(bytes(buf.tobytes())).decode("ascii")


def compute_co2_morphometrics(
    image_bgr: np.ndarray,
    segformer_result: dict[str, Any],
    cellpose_result: dict[str, Any],
    *,
    um_per_px: float | None = None,
    max_side_px: int = 1024,
    chloroplast_min_area_px: int = DEFAULT_CHLOROPLAST_MIN_AREA_PX,
    chloroplast_max_area_ratio: float = DEFAULT_CHLOROPLAST_MAX_AREA_RATIO,
) -> Co2MorphometricsResult:
    """Compute S_mes/S, S_c/S, f_ias, T_cw from upstream pipeline outputs.

    Parameters
    ----------
    image_bgr
        The raw image (HxWx3 BGR), as decoded by the endpoint.
    segformer_result
        The `result` blob of a completed `segformer_tissue` analysis.
        Must contain `polygons` and `image_shape`.
    cellpose_result
        The `result` blob of a completed `cellpose_cells` analysis.
        Must contain `cells` (list of {polygon, centroid, area_px}).
    um_per_px
        Optional pixel → µm conversion (from images.scale_um_per_px or
        a completed basic_measurement).  Metrics report both units so
        downstream analysis can pick whichever is ground-truth.
    """
    if image_bgr.ndim != 3 or image_bgr.shape[2] != 3:
        raise ValueError("image_bgr must be an HxWx3 BGR array")
    if not isinstance(segformer_result, dict):
        raise ValueError("segformer_result must be a dict")
    if not isinstance(cellpose_result, dict):
        raise ValueError("cellpose_result must be a dict")

    polygons = cast(list[dict[str, Any]], segformer_result.get("polygons") or [])
    shape = segformer_result.get("image_shape") or {}
    h_orig = int(shape.get("height_px") or image_bgr.shape[0])
    w_orig = int(shape.get("width_px") or image_bgr.shape[1])
    if h_orig != image_bgr.shape[0] or w_orig != image_bgr.shape[1]:
        # Defensive guard: segformer ran on a different image shape than
        # what we just decoded.  Use the decoded shape since cell/polygon
        # coords are in that space.
        h_orig = image_bgr.shape[0]
        w_orig = image_bgr.shape[1]

    cells = cast(list[dict[str, Any]], cellpose_result.get("cells") or [])

    longest = max(h_orig, w_orig)
    factor = max_side_px / longest if longest > max_side_px else 1.0
    h = max(int(h_orig * factor), 1)
    w = max(int(w_orig * factor), 1)
    inv_factor = 1.0 / factor

    image_ds = (
        cv2.resize(image_bgr, (w, h), interpolation=cv2.INTER_AREA)
        if factor < 1.0
        else image_bgr.copy()
    )

    notes: list[str] = []
    mesophyll_mask = _rasterise_classes(polygons, MESOPHYLL_CLASSES, h, w, factor)
    mesophyll_area_ds_px = float((mesophyll_mask > 0).sum())
    if mesophyll_area_ds_px == 0:
        notes.append(
            "no palisade/spongy polygons in the SegFormer result — "
            "S_mes/S, f_ias, T_cw all null."
        )

    section_length_ds_px = _mesophyll_section_length_ds_px(mesophyll_mask)
    section_length_px = section_length_ds_px * inv_factor
    section_length_um = (
        section_length_px * um_per_px if um_per_px is not None and section_length_px > 0 else None
    )
    thick_mean_ds, thick_median_ds = _column_thickness_ds_px(mesophyll_mask)

    mesophyll_area_px = mesophyll_area_ds_px * inv_factor * inv_factor
    mesophyll_area_um2 = (
        mesophyll_area_px * (um_per_px**2) if um_per_px is not None else None
    )
    mesophyll_stats = MesophyllStats(
        area_px=int(mesophyll_area_px),
        area_um2=mesophyll_area_um2,
        thickness_mean_um=(
            (thick_mean_ds * inv_factor) * um_per_px
            if thick_mean_ds is not None and um_per_px is not None
            else None
        ),
        thickness_median_um=(
            (thick_median_ds * inv_factor) * um_per_px
            if thick_median_ds is not None and um_per_px is not None
            else None
        ),
        section_length_um=section_length_um,
        section_length_px=section_length_px,
    )

    # Cells that fall inside the mesophyll region.  Perimeter and area
    # are evaluated in ORIGINAL-image coords (polygons come back in
    # original-grid units from cellpose_infer) so the scale conversion is
    # a simple multiply by um_per_px.
    cells_in_meso = _cells_inside(cells, mesophyll_mask, factor)

    # Raw-polygon perimeter / area sums are retained only as reporting
    # aggregates (CellAggregateStats) so operators can see per-cell-
    # level numbers.  They are NOT used for S_mes/S or f_ias below —
    # the physically-correct forms work from a rasterised union mask
    # clipped to the mesophyll polygon, which is what an IAS gas
    # molecule actually traverses.
    perim_total_px = 0.0
    area_total_px = 0.0
    for c in cells_in_meso:
        polygon = c.get("polygon") or []
        perim_total_px += _polygon_perimeter_px(polygon)
        area_total_px += _polygon_area_px(polygon)

    perim_total_um = perim_total_px * um_per_px if um_per_px is not None else None
    area_total_um2 = area_total_px * (um_per_px**2) if um_per_px is not None else None
    mean_perim_um = (
        (perim_total_um / len(cells_in_meso))
        if cells_in_meso and perim_total_um is not None
        else None
    )
    mean_area_um2 = (
        (area_total_um2 / len(cells_in_meso))
        if cells_in_meso and area_total_um2 is not None
        else None
    )
    cell_stats = CellAggregateStats(
        count=len(cells_in_meso),
        perimeter_total_um=perim_total_um,
        perimeter_total_px=perim_total_px,
        area_total_um2=area_total_um2,
        area_total_px=area_total_px,
        mean_perimeter_um=mean_perim_um,
        mean_area_um2=mean_area_um2,
    )

    # --- Rasterised masks used for the geometric CO2 metrics -------
    # Down-sampled cell-union mask of mesophyll cells, clipped to the
    # mesophyll polygon.  This collapses overlapping Cellpose instance
    # polygons (which can double-count with polygon-area sums) into a
    # single footprint, and it excludes fragments that spilled outside
    # the mesophyll region.  Every downstream CO2 metric operates on
    # this mask rather than the raw polygon list.
    cells_in_meso_mask_ds = _render_cells_mask(cells_in_meso, h, w, factor)
    cells_in_meso_mask_ds = cv2.bitwise_and(cells_in_meso_mask_ds, mesophyll_mask)
    cell_union_area_ds_px = float((cells_in_meso_mask_ds > 0).sum())

    # Cell-surface boundary pixels that face the IAS.  A cell boundary
    # pixel is "IAS-exposed" iff at least one of its 4-neighbours is in
    # the IAS mask (mesophyll ∧ ¬cells).  We compute it via a 1-px
    # dilation of the IAS mask ∩ the cell-union boundary, which is the
    # standard raster-adjacency trick.  This is the 2D analogue of the
    # Evans / Tosens "mesophyll surface exposed to intercellular air
    # space".  Shared cell-cell walls, mesophyll-epidermis boundaries,
    # and edges butting up against bundle sheath / vascular tissue are
    # NOT counted — they can't exchange gas.
    ias_mask = cv2.bitwise_and(
        mesophyll_mask, cv2.bitwise_not(cells_in_meso_mask_ds)
    )
    # Cell-union boundary via a morphological gradient (1-px outer ring).
    kernel3 = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    cell_boundary_ds = cv2.morphologyEx(
        cells_in_meso_mask_ds, cv2.MORPH_GRADIENT, kernel3
    )
    ias_dilated = cv2.dilate(ias_mask, kernel3)
    exposed_boundary_ds = cv2.bitwise_and(cell_boundary_ds, ias_dilated)
    # Each "on" pixel in exposed_boundary_ds contributes one ds-unit
    # of exposed boundary length.  Converting to original-image
    # pixels uses inv_factor (linear), not inv_factor² (area).
    l_mes_ias_ds_px = float((exposed_boundary_ds > 0).sum())
    l_mes_ias_px = l_mes_ias_ds_px * inv_factor

    # S_mes/S = L_mes,IAS / leaf section length.  Dimensionless; uses
    # the same down-sampled → original-px conversion on both sides so
    # the factor cancels out.  The original "Σ cell perimeter / section
    # length" proxy over-counted shared walls; this form matches the
    # Evans / Tosens definition more closely.
    if section_length_px > 0 and l_mes_ias_px > 0:
        s_mes_s = l_mes_ias_px / section_length_px
    else:
        s_mes_s = None
        if mesophyll_area_ds_px > 0 and not cells_in_meso:
            notes.append("no Cellpose cells inside mesophyll — S_mes/S null.")
        elif mesophyll_area_ds_px > 0 and l_mes_ias_px == 0:
            notes.append(
                "cells fill the mesophyll polygon exactly — no IAS-exposed "
                "boundary detected; S_mes/S null.  Check Cellpose coverage."
            )

    # f_ias = 1 - (cell-union area in mesophyll) / (mesophyll area).
    # Using the rasterised union instead of sum(polygon_area) avoids
    # double-counting overlapping Cellpose instances and keeps f_ias
    # bounded in [0, 1] without a clamp — because the numerator is a
    # subset of the denominator by construction.
    f_ias = (
        1.0 - (cell_union_area_ds_px / mesophyll_area_ds_px)
        if mesophyll_area_ds_px > 0
        else None
    )

    # --- Chloroplast detection + S_c/S ------------------------------
    # Run per-cell in the down-sampled LAB image for speed.  Render each
    # found chloroplast into a down-sampled mask we'll use for the
    # overlay + the S_c/S aggregation.
    image_lab_ds = cv2.cvtColor(image_ds, cv2.COLOR_BGR2LAB)
    chloroplast_mask_ds = np.zeros((h, w), dtype=np.uint8)
    chloroplast_count = 0
    chloroplast_area_ds_px = 0.0
    chloroplast_perim_ds_px = 0.0
    a_contrasts: list[float] = []

    for c in cells_in_meso:
        polygon = c.get("polygon") or []
        pts = _scale_polygon(polygon, factor)
        if pts.ndim != 2 or pts.shape[1] != 2 or pts.shape[0] < 3:
            continue
        pts[:, 0] = np.clip(pts[:, 0], 0, w - 1)
        pts[:, 1] = np.clip(pts[:, 1], 0, h - 1)
        x, y, bw, bh = cv2.boundingRect(pts)
        if bw <= 0 or bh <= 0:
            continue
        local_mask = np.zeros((bh, bw), dtype=np.uint8)
        local_pts = pts.copy()
        local_pts[:, 0] -= x
        local_pts[:, 1] -= y
        cv2.fillPoly(local_mask, [local_pts.reshape(-1, 1, 2)], color=(255,))
        lab_crop = image_lab_ds[y : y + bh, x : x + bw]
        contours, contrast = _detect_chloroplasts_in_cell(
            lab_crop,
            local_mask,
            min_area_px=chloroplast_min_area_px,
            max_area_ratio=chloroplast_max_area_ratio,
        )
        a_contrasts.append(contrast)
        for cnt in contours:
            chloroplast_count += 1
            chloroplast_area_ds_px += float(cv2.contourArea(cnt))
            chloroplast_perim_ds_px += float(cv2.arcLength(cnt, closed=True))
            shifted = cnt.copy()
            shifted[:, 0, 0] += x
            shifted[:, 0, 1] += y
            cv2.drawContours(chloroplast_mask_ds, [shifted], -1, (255,), thickness=-1)

    # Scale back to original-grid pixel units, then to µm.
    chloroplast_area_px = chloroplast_area_ds_px * inv_factor * inv_factor
    chloroplast_perim_px = chloroplast_perim_ds_px * inv_factor
    chloroplast_total_area_um2 = (
        chloroplast_area_px * (um_per_px**2) if um_per_px is not None else None
    )
    chloroplast_mean_area_um2 = (
        (chloroplast_total_area_um2 / chloroplast_count)
        if chloroplast_count > 0 and chloroplast_total_area_um2 is not None
        else None
    )
    chloroplast_total_perim_um = (
        chloroplast_perim_px * um_per_px if um_per_px is not None else None
    )
    chloroplast_coverage = (
        (chloroplast_area_px / area_total_px)
        if area_total_px > 0
        else None
    )
    chloroplast_stats = ChloroplastStats(
        count=chloroplast_count,
        total_area_px=chloroplast_area_px,
        total_area_um2=chloroplast_total_area_um2,
        mean_area_um2=chloroplast_mean_area_um2,
        total_perimeter_um=chloroplast_total_perim_um,
        coverage_of_mesophyll_cells=chloroplast_coverage,
        detection_method="lab_a_otsu",
        a_channel_contrast=float(np.mean(a_contrasts)) if a_contrasts else 0.0,
    )

    # S_c/S = fraction of the IAS-exposed cell boundary that has a
    # chloroplast lining it.  The Evans / Tosens 2-D proxy is:
    #     S_c = length of cell boundary adjacent to BOTH IAS and a
    #           chloroplast blob.
    # Raw chloroplast perimeter would count inner-cell outlines that
    # never see the gas phase; this adjacency form only counts
    # chloroplast-lined wall that a CO2 molecule in IAS actually
    # encounters.  Dilate the chloroplast mask by ~5 px (in ds coords)
    # to capture chloroplasts pressed up against the wall.
    chloroplast_near_wall_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    chloroplast_near_boundary = cv2.dilate(chloroplast_mask_ds, chloroplast_near_wall_kernel)
    chloroplast_lined_boundary = cv2.bitwise_and(exposed_boundary_ds, chloroplast_near_boundary)
    l_c_ds_px = float((chloroplast_lined_boundary > 0).sum())
    l_c_px = l_c_ds_px * inv_factor

    if section_length_px > 0 and l_c_px > 0:
        s_c_s = l_c_px / section_length_px
    else:
        s_c_s = None
        if cells_in_meso and chloroplast_count == 0:
            notes.append(
                "no chloroplasts detected via LAB a* Otsu — S_c/S null. "
                "Contrast may be insufficient (H&E-style stains) or the "
                "image lacks green-pigment tissue."
            )
        elif cells_in_meso and chloroplast_count > 0 and l_c_px == 0:
            notes.append(
                "chloroplasts detected but none sit near the IAS-exposed "
                "cell wall — S_c/S null.  Detector likely found interior "
                "blobs with no surface contact."
            )

    # --- Cell-wall thickness proxy via gap-region distance transform -
    # The DT of the cell-union complement gives, at each IAS pixel, the
    # distance to the nearest *mesophyll* cell boundary — i.e. the
    # half-width of the gap at that pixel.  Median of those distances
    # is a stable proxy for "typical wall-to-wall gap / 2" within the
    # mesophyll.  NOT a substitute for a TEM T_cw measurement, but
    # consistent across runs and sufficient for between-group C3/C4
    # comparisons which is the research question.
    #
    # Seed on `cells_in_meso_mask_ds` (the mesophyll-clipped cell
    # union), not on all Cellpose detections — otherwise epidermis or
    # bundle-sheath cells outside the mesophyll shorten the measured
    # distances near the tissue edge.
    has_cells_for_dt = bool(cells_in_meso_mask_ds.max() > 0)
    if has_cells_for_dt:
        gap_mask = ias_mask  # same mask used above
        inv_cells = cv2.bitwise_not(cells_in_meso_mask_ds)
        dt = cv2.distanceTransform(inv_cells, cv2.DIST_L2, 3)
        # Guard against inf / FLT_MAX bleed on OpenCV builds that
        # emit those values at isolated boundary pixels.
        gap_dt_raw = dt[gap_mask > 0]
        gap_dt = gap_dt_raw[np.isfinite(gap_dt_raw)]
    else:
        gap_dt = np.empty((0,), dtype=np.float32)
        notes.append(
            "no mesophyll-clipped Cellpose cells available for the distance-"
            "transform gap measurement; T_cw reported as null."
        )

    if gap_dt.size > 0:
        # Values are distances in down-sampled pixels.  Convert to
        # original-image px with inv_factor, then µm via um_per_px.
        t_cw_mean_px: float | None = float(gap_dt.mean()) * inv_factor
        t_cw_median_px: float | None = float(np.median(gap_dt)) * inv_factor
        t_cw_p95_px: float | None = float(np.quantile(gap_dt, 0.95)) * inv_factor
    else:
        t_cw_mean_px = None
        t_cw_median_px = None
        t_cw_p95_px = None

    def _to_um(px: float | None) -> float | None:
        if px is None or um_per_px is None:
            return None
        return px * um_per_px

    cell_wall_stats = CellWallStats(
        t_cw_mean_um=_to_um(t_cw_mean_px),
        t_cw_median_um=_to_um(t_cw_median_px),
        t_cw_p95_um=_to_um(t_cw_p95_px),
        t_cw_mean_px=t_cw_mean_px,
        t_cw_median_px=t_cw_median_px,
        t_cw_p95_px=t_cw_p95_px,
        gap_pixel_count=int(gap_dt.size),
    )

    overlay_png = _render_overlay_png(
        image_ds,
        mesophyll_mask,
        cells_in_meso_mask_ds,
        chloroplast_mask_ds,
    )

    return Co2MorphometricsResult(
        source_class=MESOPHYLL_CLASSES,
        downsample_factor=float(factor),
        um_per_px=um_per_px,
        image_shape={"height_px": h_orig, "width_px": w_orig},
        mesophyll=mesophyll_stats,
        mesophyll_cells=cell_stats,
        chloroplasts=chloroplast_stats,
        cell_wall=cell_wall_stats,
        s_mes_s=s_mes_s,
        s_c_s=s_c_s,
        f_ias=f_ias,
        chloroplast_overlay_png_base64=overlay_png,
        notes=notes,
    )
