"""Darcy flow solver for leaf water transport.

Where `water_path.py` (PR #6) runs Fast Marching to return a minimum-
cost travel time (unitless, useful for ranking but not a physical
quantity), this pipeline solves the actual steady-state Darcy
equation for water in the leaf:

    ∇ · (K ∇P) = 0                      on the leaf interior
    P = P_xylem                          on xylem / xylem_vessel
    P = P_stomata  (< P_xylem)           on stomata
    v · n = 0  (no-flow)                 on the outer leaf boundary

K = k/μ is the hydraulic conductivity (m² / (Pa·s)) and varies
per-tissue.  The resulting pressure field + velocity v = -K∇P let us
compute physically-meaningful quantities:

    - K_leaf  (effective leaf hydraulic conductance)
    - mean / max / p95 velocity
    - total flow through the xylem inlet and through the stomata
    - per-stomatum outflow (for uneven C4 bundle-sheath loading)

These become new scalars on `/compare`, so C3 vs C4 populations can be
tested with Welch + Mann-Whitney + Hedges' g the same way the
morphometrics (PR #10) already are.

Discretisation
--------------
Standard cell-centered finite volume with **harmonic-mean** face
conductivity (canonical for piecewise-discontinuous K — an arithmetic
mean would smear xylem's high K into neighbouring mesophyll).  The
linear system is assembled with scipy.sparse (lil_matrix build, csc
solve) and dispatched to `scipy.sparse.linalg.spsolve`.  For a
typical 1024 x 1024 tissue grid the matrix is ~10^6 x 10^6 with 5
non-zeros per row; spsolve handles it in <10 s on CPU.

The solver is CPU-bound and deterministic — run via `asyncio.to_thread`
from the FastAPI endpoint so the event loop stays responsive.
"""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass, field
from typing import Any, cast

import cv2
import numpy as np
from scipy import sparse
from scipy.sparse.linalg import spsolve

# Water viscosity at 25 °C ≈ 8.9e-4 Pa·s.  We report K (conductivity)
# = k/μ in m² / (Pa·s); the solver can absorb μ into K since it's
# constant in the temperature regime plant physiology works in.
WATER_VISCOSITY_PA_S = 8.9e-4

# Liquid water density at 25 °C ≈ 997 kg/m³.  Used to convert
# volumetric flux q [m³/(s·m-depth) = m²/s] into mass flux M [kg/(s·m-depth)]
# at report time, so the publicly-named ``flow`` / ``flow_in`` /
# ``flow_out`` / ``k_leaf`` fields actually carry the kg-based units
# their docstrings (and the literature on K_leaf) advertise.  The
# round-3 docs audit caught that the previous implementation reported
# the volumetric flux under a kg-flux label.
WATER_DENSITY_KG_M3 = 997.0

# Per-class permeability k (m²) scaled relative to mesophyll (palisade).
# These are heuristic — real xylem vessels are >3 orders of magnitude
# more permeable than cell-wall water.  Absolute values don't need to
# match field measurements for cross-group comparison (C3 vs C4) since
# both groups use the same K_leaf definition; ratios are what matter.
# Operators override per-class via the request body.
#
# Values are m² and become K = k/μ at solve time.  Background (outside
# leaf) gets effectively zero so no-flow is enforced by construction.
DEFAULT_PERMEABILITY: dict[str, float] = {
    "xylem_vessel": 5.0e-11,   # water moves very freely
    "xylem": 1.0e-11,
    "phloem": 1.0e-16,         # near-zero, carries sugars not water
    "bundle_sheath": 2.0e-14,
    "palisade": 1.0e-14,
    "spongy": 1.5e-14,
    "intercellular": 1.0e-16,  # air; liquid flow negligible
    "stomata": 1.0e-13,        # used only as sink Dirichlet BC
    "upper_epidermis": 1.0e-16,
    "lower_epidermis": 1.0e-16,
    "other": 5.0e-15,
}
# Effectively no flow; ε instead of zero so matrix stays SPD.
BACKGROUND_PERMEABILITY = 1.0e-18

# Default Dirichlet pressures (Pa).  Sign convention: xylem carries
# higher (less negative) water potential; stomata at the evaporating
# face carry lower.  Values are chosen to give a ΔP ~1 MPa, a typical
# mesophyll gradient under moderate transpiration.  Operators can
# override via the request body.
DEFAULT_P_XYLEM = 0.0
DEFAULT_P_STOMATA = -1.0e6

# Maximum resolution of the solver grid.  A 2048 px microscope image
# has ~4 million cells → matrix ~4M x 4M; spsolve wouldn't finish.
# Down-sample to keep the LU factorisation tractable on CPU.
DEFAULT_MAX_SIDE_PX = 1024


@dataclass(frozen=True)
class StomatumFlow:
    centroid: list[float]
    flow: float            # kg / s / m-depth (2-D integrated)
    mean_velocity: float   # m/s (average over the stomatum cells)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DarcyResult:
    source_class: str          # 'xylem_vessel' | 'xylem'
    sink_class: str            # always 'stomata'
    p_xylem_pa: float
    p_stomata_pa: float
    pressure_drop_pa: float
    # Pressure field stats (in Pa; None when solve failed).
    pressure_min_pa: float | None
    pressure_max_pa: float | None
    # Velocity field stats (m/s).
    velocity_mean: float | None
    velocity_p95: float | None
    velocity_max: float | None
    # Integrated flows.  Steady-state continuity means flow_in ≈
    # flow_out; the pair is reported separately so any numerical
    # imbalance shows up directly in the result.
    total_flow_in: float       # kg / s per metre-depth (integrated over xylem outlet)
    total_flow_out: float      # kg / s per metre-depth (integrated over stomata inlet)
    # Effective leaf hydraulic conductance:
    #     K_leaf = |flow_out| / pressure_drop
    # Units: kg / (s · Pa · m-depth).  Reported so C3/C4 groups can
    # be compared directly in /compare.
    k_leaf: float | None
    stomata_outflows: list[StomatumFlow] = field(default_factory=list)
    pressure_png_base64: str = ""
    velocity_png_base64: str = ""
    heatmap_shape: tuple[int, int] = (0, 0)
    downsample_factor: float = 1.0
    permeability: dict[str, float] = field(default_factory=dict)
    # Free-form diagnostics — e.g. "no xylem vessel polygons; fell
    # back to xylem" or "solver converged with residual 1.2e-8".
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _heatmap_to_png_base64(
    values: np.ndarray, alpha_mask: np.ndarray | None = None
) -> str:
    """Render a 2-D scalar field to a magma-style PNG (base64).

    Copy of the helper in water_path.py — kept duplicated rather than
    factored into a shared utility so each pipeline's heatmap can
    evolve its palette / alpha logic independently.
    """
    finite = np.isfinite(values)
    if not finite.any():
        return ""
    vmin = float(values[finite].min())
    vmax = float(values[finite].max())
    span = max(vmax - vmin, 1e-12)
    norm = np.zeros_like(values, dtype=np.float32)
    norm[finite] = (values[finite] - vmin) / span
    norm[~finite] = 0.0
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
    bgra = rgba[..., [2, 1, 0, 3]]
    ok, buf = cv2.imencode(".png", bgra)
    if not ok:
        return ""
    return base64.b64encode(bytes(buf.tobytes())).decode("ascii")


def _sanitise_overrides(
    override: dict[str, float] | None,
) -> dict[str, float]:
    """Same shape as water_path's override check: keep only finite
    strictly-positive values.  Anything else silently drops."""
    out: dict[str, float] = {}
    for k, v in (override or {}).items():
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv > 0 and np.isfinite(fv):
            out[k] = fv
    return out


def compute_darcy(
    segformer_result: dict[str, Any],
    *,
    um_per_px: float | None = None,
    max_side_px: int = DEFAULT_MAX_SIDE_PX,
    p_xylem_pa: float = DEFAULT_P_XYLEM,
    p_stomata_pa: float = DEFAULT_P_STOMATA,
    permeability_override: dict[str, float] | None = None,
) -> DarcyResult:
    """Solve steady-state Darcy flow on the tissue map.

    Parameters
    ----------
    segformer_result
        The `result` blob of a completed `segformer_tissue` analysis;
        must carry `polygons` and `image_shape`.
    um_per_px
        Optional pixel → micrometre conversion.  Needed to emit
        velocities in m/s rather than arbitrary units.
    max_side_px
        Down-sample target so the linear system stays solvable on CPU.
    p_xylem_pa, p_stomata_pa
        Dirichlet BC on xylem / stomata regions.  Gradient must be
        positive (xylem > stomata); if reversed, flow just reverses
        sign, which is still useful for debugging the pipeline.
    permeability_override
        Per-class permeability overrides (m²).  Merged into the
        defaults; non-finite or non-positive values are dropped.
    """
    # Deferred import so water_path's rasteriser becomes the single
    # source of truth for polygon → mask conversion.  Saves us from
    # drift when the class taxonomy gains a new label.
    from app.pipeline.water_path import _rasterise_class

    if not isinstance(segformer_result, dict):
        raise ValueError("segformer_result must be the SegFormer result blob")

    polygons = cast(list[dict[str, Any]], segformer_result.get("polygons") or [])
    shape = segformer_result.get("image_shape") or {}
    h_orig = int(shape.get("height_px") or 0)
    w_orig = int(shape.get("width_px") or 0)
    if h_orig <= 0 or w_orig <= 0:
        raise ValueError("segformer_result lacks usable image_shape")
    if p_xylem_pa == p_stomata_pa:
        raise ValueError("p_xylem_pa and p_stomata_pa must differ — no gradient otherwise")

    longest = max(h_orig, w_orig)
    factor = max_side_px / longest if longest > max_side_px else 1.0
    h = max(int(h_orig * factor), 1)
    w = max(int(w_orig * factor), 1)
    inv_factor = 1.0 / factor
    # Cell spacing, in metres.  Fall back to 1 µm if no scale — the
    # absolute velocity becomes relative, but K_leaf remains useful
    # as a ratio between images processed the same way.
    dx_m = (inv_factor * (um_per_px or 1.0)) * 1e-6

    # ----- tissue masks & source selection --------------------------
    has_vessel = any(p.get("class_key") == "xylem_vessel" for p in polygons)
    source_class = "xylem_vessel" if has_vessel else "xylem"
    source_mask = _rasterise_class(polygons, source_class, h, w, factor)
    sink_mask = _rasterise_class(polygons, "stomata", h, w, factor)

    if source_mask.max() == 0:
        raise ValueError(
            "no xylem / xylem_vessel polygons in the SegFormer result; "
            "Darcy flow needs a source boundary"
        )
    if sink_mask.max() == 0:
        raise ValueError(
            "no stomata polygons in the SegFormer result; Darcy flow "
            "needs a sink boundary"
        )

    # ----- per-cell permeability map --------------------------------
    sanitised_override = _sanitise_overrides(permeability_override)
    permeability: dict[str, float] = {**DEFAULT_PERMEABILITY, **sanitised_override}
    # K[i, j] in m² / (Pa·s).  Start at BACKGROUND then overwrite.
    k_field = np.full(
        (h, w),
        BACKGROUND_PERMEABILITY / WATER_VISCOSITY_PA_S,
        dtype=np.float64,
    )
    leaf_mask = np.zeros((h, w), dtype=np.uint8)
    # Paint order matches water_path so later classes (vessels, sinks)
    # overwrite broader regions underneath (palisade, spongy).
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
        k_class = permeability.get(cls_key, BACKGROUND_PERMEABILITY)
        k_field = np.where(cls_mask > 0, k_class / WATER_VISCOSITY_PA_S, k_field)
        leaf_mask = np.maximum(leaf_mask, cls_mask)

    # ----- boundary mask + RHS --------------------------------------
    # A cell is Dirichlet if it sits on xylem OR stomata.  Everywhere
    # else we enforce the finite-volume Laplacian.
    dirichlet = np.zeros((h, w), dtype=bool)
    dirichlet_value = np.zeros((h, w), dtype=np.float64)
    dirichlet[source_mask > 0] = True
    dirichlet_value[source_mask > 0] = p_xylem_pa
    dirichlet[sink_mask > 0] = True
    dirichlet_value[sink_mask > 0] = p_stomata_pa

    # ----- assemble sparse system -----------------------------------
    n = h * w

    # Harmonic-mean face conductivity between cell (y,x) and (y',x').
    # The 2/(1/a + 1/b) form vanishes when either side is tiny — that's
    # the property we rely on to push no-flow at the BACKGROUND interface.
    def _harmonic(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        denom = a + b
        with np.errstate(divide="ignore", invalid="ignore"):
            res = np.where(denom > 0, 2.0 * a * b / denom, 0.0)
        return res

    kx = _harmonic(k_field[:, :-1], k_field[:, 1:])   # face between (y, x) and (y, x+1)
    ky = _harmonic(k_field[:-1, :], k_field[1:, :])   # face between (y, x) and (y+1, x)

    # COO accumulation: faster to build than LIL for large grids.
    rows: list[np.ndarray] = []
    cols: list[np.ndarray] = []
    data: list[np.ndarray] = []

    # Interior Laplacian: for each cell (y,x), collect the 4
    # face-conductivities and emit both the off-diagonal and diagonal
    # contributions.  dx_m² comes out of the cancellation between
    # face area and cell length, leaving a dimensionally-correct
    # [K]·[ΔP]/[dx²] = source term.  Since source = 0 here, we just
    # balance fluxes.
    idx_grid = np.arange(n).reshape(h, w)

    # --- East/West faces (x+1 and x-1 neighbours) -------------------
    # Face between (y, x) and (y, x+1):
    #   adds +kx * P_right to row(y,x), -kx * P_center to row(y,x)
    #   adds +kx * P_left  to row(y,x+1), -kx * P_center_right to row(y,x+1)
    center_e = idx_grid[:, :-1].ravel()
    right_w = idx_grid[:, 1:].ravel()
    kx_flat = kx.ravel()
    rows.extend([center_e, center_e, right_w, right_w])
    cols.extend([right_w, center_e, center_e, right_w])
    data.extend([kx_flat, -kx_flat, kx_flat, -kx_flat])

    # --- North/South faces ------------------------------------------
    center_n = idx_grid[:-1, :].ravel()
    below_n = idx_grid[1:, :].ravel()
    ky_flat = ky.ravel()
    rows.extend([center_n, center_n, below_n, below_n])
    cols.extend([below_n, center_n, center_n, below_n])
    data.extend([ky_flat, -ky_flat, ky_flat, -ky_flat])

    rows_arr = np.concatenate(rows)
    cols_arr = np.concatenate(cols)
    data_arr = np.concatenate(data)
    # `mat` and `rhs` are the standard A·x = b system; named in
    # software-style (rather than mathematical A/b) so ruff's N806
    # rule doesn't fire on every line of solver code.
    mat = sparse.csr_matrix((data_arr, (rows_arr, cols_arr)), shape=(n, n))

    # Overwrite Dirichlet rows with P = boundary value (clearing the
    # row of Laplacian contributions and pinning the diagonal to 1).
    rhs = np.zeros(n, dtype=np.float64)
    dirichlet_flat = dirichlet.ravel()
    dirichlet_idx = np.where(dirichlet_flat)[0]
    # Clear Dirichlet rows of their Laplacian contributions.
    mat = mat.tolil()
    for i in dirichlet_idx:
        mat.rows[i] = [i]
        mat.data[i] = [1.0]
    mat = mat.tocsc()
    rhs[dirichlet_idx] = dirichlet_value.ravel()[dirichlet_idx]

    # ----- solve ----------------------------------------------------
    try:
        pressure_flat = spsolve(mat, rhs)
    except Exception as exc:
        raise RuntimeError(f"Darcy solve failed: {exc}") from exc
    pressure = pressure_flat.reshape(h, w)
    # Replace any non-finite values from near-singular Dirichlet regions.
    pressure = np.where(np.isfinite(pressure), pressure, p_stomata_pa)

    # ----- velocity field -------------------------------------------
    # Central-difference gradient on the grid.  dx_m cancels the
    # denominator; K multiplies in.  v = -K grad(P), reported as m/s
    # (when um_per_px supplied).
    grad_y, grad_x = np.gradient(pressure, dx_m)
    vx = -k_field * grad_x
    vy = -k_field * grad_y
    v_mag = np.sqrt(vx * vx + vy * vy)
    # Stomata / xylem Dirichlet cells get an artificial sharp
    # gradient at the BC interface, AND xylem cells carry a much
    # higher K than mesophyll, so v_mag inside them spikes to values
    # 100-1000x the bulk flow.  Those cells aren't part of the
    # diffusion path we're measuring -- restrict the velocity stats
    # to non-Dirichlet leaf interior so the reported mean reflects
    # the actual mesophyll velocity, not the BC artefact.  K_leaf
    # below uses integrated boundary flow, which is unaffected.
    interior = (leaf_mask > 0) & (~dirichlet)
    v_interior = v_mag[interior]
    if v_interior.size > 0 and np.isfinite(v_interior).any():
        finite_v = v_interior[np.isfinite(v_interior)]
        velocity_mean: float | None = float(finite_v.mean())
        velocity_p95: float | None = float(np.quantile(finite_v, 0.95))
        velocity_max: float | None = float(finite_v.max())
    else:
        velocity_mean = velocity_p95 = velocity_max = None

    # ----- integrated flows -----------------------------------------
    # Boundary flux = sum of SIGNED NORMAL face fluxes across each
    # face that separates a Dirichlet cell from a non-Dirichlet leaf
    # neighbour.  Per face the finite-volume flux is
    #
    #     q_face = K_face * (P_dirichlet - P_neighbor)
    #
    # which is the discrete Darcy law on a unit-length face (dx
    # cancels the gradient denominator against the face area in 2-D
    # per-metre-depth).  Summing over a leaving boundary gives a
    # SIGNED outflow in m^2/s — positive when flow exits the source
    # / enters the sink, negative if the gradient is reversed for
    # debugging.  The earlier `Σ |v_mag| · dx` approximation mixed
    # tangential velocity, included background-ring cells, and lost
    # sign information; the headline K_leaf was meaningfully off as
    # a result.  The signed-flux form below matches conservation of
    # mass to machine precision.
    # Non-Dirichlet leaf interior — used to gate the boundary-flux
    # integration so Dirichlet↔Dirichlet faces (e.g. an unlikely
    # xylem-touches-stomata case after down-sampling) don't get
    # counted as a BC-to-BC shortcut that would inflate K_leaf.
    interior_leaf = (leaf_mask > 0) & (~dirichlet)

    # Re-use the kx / ky face-conductivity grids built during assembly.
    def _boundary_outflow(mask_bool: np.ndarray) -> float:
        """Signed flux LEAVING `mask_bool` across faces it shares with
        a non-Dirichlet leaf interior cell (mask=True for the
        Dirichlet region in question; the opposite side must lie in
        `interior_leaf` so a touching second BC doesn't pollute the
        sum)."""
        total = 0.0
        # East-west faces: face (y, x) sits between cell (y, x) and
        # cell (y, x+1).  left_only means the left cell is in the
        # mask and the right cell is in the interior leaf — flux
        # leaves the mask toward the right at rate K_face *
        # (P_left - P_right).  right_only is the mirror case.
        int_left = interior_leaf[:, :-1]
        int_right = interior_leaf[:, 1:]
        left = mask_bool[:, :-1]
        right = mask_bool[:, 1:]
        left_only = left & int_right
        if left_only.any():
            total += float(
                np.sum(
                    kx[left_only]
                    * (pressure[:, :-1][left_only] - pressure[:, 1:][left_only])
                )
            )
        right_only = right & int_left
        if right_only.any():
            total += float(
                np.sum(
                    kx[right_only]
                    * (pressure[:, 1:][right_only] - pressure[:, :-1][right_only])
                )
            )
        # North-south faces, same logic along axis 0.
        int_top = interior_leaf[:-1, :]
        int_bottom = interior_leaf[1:, :]
        top = mask_bool[:-1, :]
        bottom = mask_bool[1:, :]
        top_only = top & int_bottom
        if top_only.any():
            total += float(
                np.sum(
                    ky[top_only]
                    * (pressure[:-1, :][top_only] - pressure[1:, :][top_only])
                )
            )
        bottom_only = bottom & int_top
        if bottom_only.any():
            total += float(
                np.sum(
                    ky[bottom_only]
                    * (pressure[1:, :][bottom_only] - pressure[:-1, :][bottom_only])
                )
            )
        return total

    source_bool = source_mask > 0
    sink_bool = sink_mask > 0
    # `_boundary_outflow` returns the volumetric flux [m²/s = m³/(s·m-depth)].
    # Multiply by water density to obtain the publicly-advertised mass
    # flux units of kg/(s·m-depth).
    flow_in = _boundary_outflow(source_bool) * WATER_DENSITY_KG_M3
    # `_boundary_outflow(sink)` returns flux LEAVING sink — water
    # entering sink from leaf = - that.  Negate to keep both numbers
    # positive in the typical configuration (P_xylem > P_stomata).
    flow_out = -_boundary_outflow(sink_bool) * WATER_DENSITY_KG_M3

    pressure_drop = abs(p_xylem_pa - p_stomata_pa)
    k_leaf: float | None
    if pressure_drop > 0 and flow_out > 0 and np.isfinite(flow_out):
        # K_leaf = flow_out / ΔP — units kg/(s·Pa·m-depth) once flow_out
        # is in kg/(s·m-depth).
        k_leaf = flow_out / pressure_drop
    else:
        k_leaf = None

    # Per-stomatum outflow: same signed-flux integration but masked
    # to each individual stomata polygon, so the C4 bundle-sheath
    # story (uneven stomatal loading) can be read off directly.
    stomata_polygons = [p for p in polygons if p.get("class_key") == "stomata"]
    per_stomatum: list[StomatumFlow] = []
    for poly in stomata_polygons:
        coords = poly.get("polygon")
        if not isinstance(coords, list) or len(coords) < 3:
            continue
        arr = np.asarray(coords, dtype=np.float64)
        cx_orig = float(arr[:, 0].mean())
        cy_orig = float(arr[:, 1].mean())
        single_mask = np.zeros((h, w), dtype=np.uint8)
        pts = (arr * factor).round().astype(np.int32)
        pts[:, 0] = np.clip(pts[:, 0], 0, w - 1)
        pts[:, 1] = np.clip(pts[:, 1], 0, h - 1)
        cv2.fillPoly(single_mask, [pts.reshape(-1, 1, 2)], color=(255,))
        single_bool = single_mask > 0
        # Flow ENTERING this stomatum = - signed flux LEAVING it.
        # Multiply by water density so the per-stomatum number
        # carries the same kg/(s·m-depth) units as the aggregate.
        single_flow = -_boundary_outflow(single_bool) * WATER_DENSITY_KG_M3
        # Sample mean velocity in the leaf interior cells DIRECTLY
        # adjacent to this stomatum (1-px outer ring intersected
        # with non-Dirichlet leaf).  v_mag inside the stomatum body
        # itself is dominated by the artificial Dirichlet gradient
        # and was the same artefact excluded from the global
        # velocity stats; sampling the adjacent interior gives a
        # physically meaningful "what speed is water arriving at
        # this stomatum" number.
        single_outer = (
            np.zeros((h, w), dtype=bool)
        )
        # Build the 1-cell outer ring without an OpenCV dependency
        # (cheap, only 4 row-shifts on a small mask).
        single_outer[:-1, :] |= single_bool[1:, :]
        single_outer[1:, :] |= single_bool[:-1, :]
        single_outer[:, :-1] |= single_bool[:, 1:]
        single_outer[:, 1:] |= single_bool[:, :-1]
        single_outer &= ~single_bool
        single_outer &= interior_leaf
        ring_v = v_mag[single_outer]
        ring_v = ring_v[np.isfinite(ring_v)]
        per_stomatum.append(
            StomatumFlow(
                centroid=[cx_orig, cy_orig],
                flow=single_flow,
                mean_velocity=float(ring_v.mean()) if ring_v.size else 0.0,
            )
        )

    # ----- heatmaps -------------------------------------------------
    pressure_png = _heatmap_to_png_base64(pressure, alpha_mask=leaf_mask)
    v_mag_for_png = np.where(np.isfinite(v_mag), v_mag, 0.0)
    velocity_png = _heatmap_to_png_base64(v_mag_for_png, alpha_mask=leaf_mask)

    # Pressure stats use the FULL leaf (including Dirichlet bands)
    # because xylem and stomata pressures ARE the imposed BC values
    # and we want the operator to see them directly in the reported
    # min/max.  Velocity stats above use the non-Dirichlet interior.
    full_leaf = leaf_mask > 0
    finite_pressure = pressure[full_leaf]
    finite_pressure = finite_pressure[np.isfinite(finite_pressure)]
    pressure_min = float(finite_pressure.min()) if finite_pressure.size else None
    pressure_max = float(finite_pressure.max()) if finite_pressure.size else None

    notes: list[str] = []
    if not has_vessel:
        notes.append("xylem_vessel polygons absent; used xylem as source boundary instead")
    # Continuity check: in steady state with the signed-face-flux
    # integration the imbalance should sit at floating-point noise
    # (1e-10 to 1e-8 relative).  Anything materially above 0.1 %
    # likely means the solver hit a near-singular sub-domain — flag
    # it so the operator looks rather than trusts the K_leaf number.
    # Threshold tightened from 5% in round-2 since the old loose bound
    # was set when the boundary integration used unsigned ring sums.
    if flow_in > 0 and flow_out > 0:
        imbalance = abs(flow_in - flow_out) / max(flow_in, flow_out)
        if imbalance > 0.001:
            notes.append(
                f"continuity imbalance between xylem inflow and stomata outflow "
                f"is {imbalance:.2%}; expected near floating-point precision "
                "with the discrete face-flux integration — likely a "
                "near-singular subdomain or disconnected leaf region"
            )

    return DarcyResult(
        source_class=source_class,
        sink_class="stomata",
        p_xylem_pa=p_xylem_pa,
        p_stomata_pa=p_stomata_pa,
        pressure_drop_pa=pressure_drop,
        pressure_min_pa=pressure_min,
        pressure_max_pa=pressure_max,
        velocity_mean=velocity_mean,
        velocity_p95=velocity_p95,
        velocity_max=velocity_max,
        total_flow_in=flow_in,
        total_flow_out=flow_out,
        k_leaf=k_leaf,
        stomata_outflows=per_stomatum,
        pressure_png_base64=pressure_png,
        velocity_png_base64=velocity_png,
        heatmap_shape=(h, w),
        downsample_factor=float(factor),
        permeability=permeability,
        notes=notes,
    )
