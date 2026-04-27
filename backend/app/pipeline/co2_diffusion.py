"""CO2 reaction-diffusion solver for the leaf interior.

Where Darcy (PR #12) treated water flow under a pressure BC,
this solves the CO2 concentration field under a stomatal Dirichlet
BC plus a per-cell carboxylation (reaction) term.  Two reaction
kinetics are supported:

  * ``michaelis_menten`` (DEFAULT) — full Farquhar–von Caemmerer–
    Berry Rubisco-limited carboxylation:

        R(C) = V_cmax_vol * (C - Gamma_star) / (C + K_eff)

    with K_eff = K_c (1 + O / K_o) and Bernacchi 2001 kinetics
    constants at 25 °C.  Because R depends nonlinearly on C, the
    PDE is solved by Picard iteration:

        Linearize R around C^(k-1):
            R(C) ≈ a^(k-1) + b^(k-1) · C
        with
            b = R'(C^(k-1))
            a = R(C^(k-1)) − b · C^(k-1)

        Solve  ∇·(D∇C) − b · C = a   for C^(k)
        Iterate until ||C^(k) − C^(k-1)||_∞ < tol.

    In steady state the volume integral of R equals the boundary
    influx through the stomatal Dirichlet faces (mass conservation),
    so A_net comes from either route — we use the volumetric form
    because it's more direct.

  * ``linear`` (legacy) — first-order draw R(C) = r · C used in the
    PR #13a prototype.  Kept as an option for sensitivity studies
    against the M-M form, and for synthetic test images where the
    closed-form R(C) makes algebraic checks easier.

The PDE itself (steady, conservative finite volume on a regular
pixel grid):

    nabla . (D nabla C) − R(C) = 0     on the leaf interior
    C = Ci                              on stomata (Dirichlet)
    flux . n = 0                        on the outer leaf boundary

D = CO2 diffusivity (m^2/s), piecewise per tissue.  In the gas phase
(IAS, stomatal pore) D ~= 1.6e-5; in liquid water through cell walls
and cytosol it drops by ~4 orders of magnitude to ~1.79e-9.  The
reaction term lives only in cells that fix CO2 (chloroplast pixels
when co2_morphometrics has run; mesophyll cells otherwise).

Outputs the CO2 concentration field, a drawdown heatmap (Ci - C),
the mean Cc inside the chloroplast region, the total CO2 flux into
the chloroplast region (the net assimilation A_net), and the
mesophyll conductance proxy:

    g_m_proxy = A_net / (leaf_section_length · (Ci - Cc_mean))
              [mol m^-2 s^-1 Pa^-1]

For the Farquhar parameter fit against measured LI-COR A–Cc curves
see ``pipeline/gm_fit.py`` — this module produces the Cc field that
fit consumes.
"""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass, field
from typing import Any, cast

import cv2
import numpy as np
from scipy import sparse
from scipy.sparse.linalg import spsolve

# CO2 diffusivity (m^2 / s).  Gas phase: free-air value.  Liquid
# (cell wall + cytosol): ~4 orders of magnitude smaller.  Background
# (outside the leaf) gets epsilon to keep the matrix SPD without
# special-casing.  Operators override per-class via the request body.
DEFAULT_DIFFUSIVITY: dict[str, float] = {
    "intercellular": 1.6e-5,   # CO2 in air at 25C
    "stomata": 1.6e-5,         # gas pore (Dirichlet anyway, so D matters
                                #            only on the inner boundary face)
    "palisade": 1.79e-9,       # CO2 in water through cell wall + cytosol
    "spongy": 1.79e-9,
    "bundle_sheath": 1.79e-9,
    "xylem": 1.79e-9,
    "xylem_vessel": 1.79e-9,
    "phloem": 1.79e-9,
    "upper_epidermis": 1.79e-10,  # waxy cuticle barrier
    "lower_epidermis": 1.79e-10,
    "other": 1.79e-9,
}
BACKGROUND_DIFFUSIVITY = 1.0e-15

# First-order CO2 consumption rate (1/s) in chloroplast cells.  Set
# to give a Cc drawdown of ~10-50 Pa relative to the standard 25 Pa
# Ci in normal mesophyll geometry (chosen by sensitivity analysis on
# typical leaf cross-section sizes).  Used only when
# ``kinetics_mode="linear"``.
DEFAULT_REACTION_RATE = 1.0

# Default ambient Ci.  ~25 Pa partial pressure at 1 atm and 250 ppm
# CO2 — close to typical mesophyll Ci under normal photosynthesis.
DEFAULT_CI_PA = 25.0

# ---------------------------------------------------------------------
# Bernacchi 2001 (Plant Cell Environ 24:253-259) kinetics constants
# at 25 °C, expressed as PARTIAL PRESSURES (Pa) so the PDE state
# variable C [Pa] enters R(C) directly without unit conversion.
# These are the "consensus C3" values; operators can override per-leaf.
# ---------------------------------------------------------------------
DEFAULT_KC_PA = 27.238           # Rubisco Michaelis constant for CO2
DEFAULT_KO_PA = 16582.0          # Rubisco Michaelis constant for O2
DEFAULT_O2_PA = 21000.0          # ambient O2 partial pressure at 1 atm
DEFAULT_GAMMA_STAR_PA = 3.743    # CO2 compensation point in absence of Rd

# Volumetric V_cmax inside the chloroplast region.  This is the
# carboxylation capacity per cubic metre of *sink tissue*, NOT per
# unit leaf area — a derived quantity that depends on the leaf's
# chloroplast layer thickness.  Choose:
#
#     V_cmax_vol = V_cmax_area / (effective_chloroplast_layer_thickness)
#
# For a typical C3 leaf with V_cmax_area = 80 µmol/m²/s and a
# 25-µm-equivalent chloroplast layer, V_cmax_vol ≈ 3 mol/m³/s.
# The 1.0 mol/m³/s default is a conservative midpoint that gives a
# Cc drawdown of 5–15 Pa for typical mesophyll geometries; tweak
# upward for high-V_cmax C3 species (e.g. Helianthus, soybean) or
# downward for shade leaves.
DEFAULT_VCMAX_PER_VOLUME = 1.0

# Picard iteration controls for the M-M solve.  Convergence is
# guaranteed by the monotonicity of R(C) on C ≥ Γ*; in practice
# the iteration converges in ~5–15 sweeps to the default tolerance.
DEFAULT_PICARD_MAX_ITER = 50
DEFAULT_PICARD_TOL_PA = 1e-4

# Allowed kinetics modes — kept as a tuple so a typo at the call site
# fails loudly rather than silently falling back to "linear".
KINETICS_MODES = ("michaelis_menten", "linear")
DEFAULT_KINETICS_MODE = "michaelis_menten"

# Maximum solver grid side.  Same trade-off as Darcy: spsolve on
# 1024^2 is tractable on CPU, larger gets slow.
DEFAULT_MAX_SIDE_PX = 1024

# Pixel-classes that act as the chloroplast / sink region in the
# fallback path when co2_morphometrics didn't run.  Mesophyll cells
# are where Rubisco lives in C3 leaves and where the Calvin cycle
# happens in the bundle sheath of C4 leaves; spongy + palisade is a
# coarse but workable approximation in the absence of an explicit
# chloroplast mask.
FALLBACK_CHLOROPLAST_CLASSES: tuple[str, ...] = ("palisade", "spongy")


@dataclass(frozen=True)
class StomatumDrawdown:
    centroid: list[float]
    cc_mean_pa: float | None      # mean CO2 in the 1-cell ring on the leaf side
    drawdown_pa: float | None     # Ci - cc_mean_pa
    flow_in: float                # CO2 flowing INTO the stomatum from leaf
                                  # (negative when CO2 is being supplied to leaf, +ve here)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Co2DiffusionResult:
    sink_class: str               # "chloroplast" (when we had a co2_morphometrics
                                  # overlay) or "mesophyll_cells" (fallback)
    ci_pa: float
    cc_mean_pa: float | None
    drawdown_mean_pa: float | None
    drawdown_max_pa: float | None
    a_net: float                  # total CO2 fixed in the chloroplast region
                                  # [mol / (s · m-depth)]  (2-D per-metre-depth
                                  # integral of either the volumetric reaction
                                  # term — preferred for M-M — or the signed
                                  # face flux through the sink boundary).
    leaf_section_length_m: float  # major axis of the leaf cross-section in
                                  # metres — the denominator used to turn the
                                  # per-metre-depth flux into a per-unit-leaf-
                                  # area flux for g_m.
    g_m_proxy: float | None       # mesophyll conductance proxy
                                  # = A_net / (leaf_length · (Ci - Cc))
                                  # [mol m^-2 s^-1 Pa^-1]; matches the
                                  # Farquhar fit's `g_m` units.
    kinetics_mode: str = DEFAULT_KINETICS_MODE  # "michaelis_menten" | "linear"
    # FvCB kinetics constants used for this run (Pa).  Echoed back so
    # the operator can reconstruct the exact parameter set from the
    # persisted analysis row.
    vcmax_per_volume_mol_m3_s: float = DEFAULT_VCMAX_PER_VOLUME
    kc_pa: float = DEFAULT_KC_PA
    ko_pa: float = DEFAULT_KO_PA
    o2_pa: float = DEFAULT_O2_PA
    gamma_star_pa: float = DEFAULT_GAMMA_STAR_PA
    # Picard convergence diagnostics.  `picard_iterations == 0` means
    # the linear-mode solver was used (no nonlinear iteration needed).
    picard_iterations: int = 0
    picard_residual_pa: float = 0.0
    stomata_drawdowns: list[StomatumDrawdown] = field(default_factory=list)
    concentration_png_base64: str = ""
    drawdown_png_base64: str = ""
    heatmap_shape: tuple[int, int] = (0, 0)
    downsample_factor: float = 1.0
    diffusivity: dict[str, float] = field(default_factory=dict)
    reaction_rate: float = DEFAULT_REACTION_RATE  # only meaningful in linear mode
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _heatmap_to_png_base64(
    values: np.ndarray, alpha_mask: np.ndarray | None = None
) -> str:
    """Magma-style PNG.  Same shape as darcy._heatmap_to_png_base64;
    duplicated so each pipeline can evolve its palette independently."""
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


def _leaf_section_length_m(leaf_mask: np.ndarray, dx_m: float) -> float:
    """Leaf section length in metres — the denominator that turns the
    2-D per-metre-depth flux (mol/(s·m)) into a per-unit-leaf-area
    flux (mol/(m²·s)), which is what the standard g_m definition
    requires.  Uses the major axis of the minimum-area rectangle
    around the leaf mask so the measurement is rotation-invariant
    (same helper idea as morphometrics_co2's section_length)."""
    ys, xs = np.where(leaf_mask > 0)
    if xs.size == 0:
        return 0.0
    if xs.size < 3:
        return float(xs.max() - xs.min() + 1) * dx_m
    pts = np.column_stack([xs, ys]).astype(np.float32)
    rect = cv2.minAreaRect(pts)
    (_cx, _cy), (rw, rh), _angle = rect
    return float(max(rw, rh)) * dx_m


def _sanitise_overrides(
    override: dict[str, float] | None,
) -> dict[str, float]:
    """Drop non-finite or non-positive entries silently — same idiom
    as Darcy's permeability override sanitiser."""
    out: dict[str, float] = {}
    for k, v in (override or {}).items():
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv > 0 and np.isfinite(fv):
            out[k] = fv
    return out


def _decode_chloroplast_mask(
    co2_morph_result: dict[str, Any] | None, h_orig: int, w_orig: int, factor: float
) -> np.ndarray | None:
    """Decode the chloroplast overlay PNG embedded in a co2_morphometrics
    result, resize to the down-sampled solver grid.  Returns None when
    the field is missing — caller falls back to mesophyll classes."""
    if not isinstance(co2_morph_result, dict):
        return None
    b64 = co2_morph_result.get("chloroplast_overlay_png_base64")
    if not isinstance(b64, str) or not b64:
        return None
    raw = base64.b64decode(b64)
    arr = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
    if img is None:
        return None
    # The overlay is RGBA; chloroplast pixels are the bright magenta
    # (200, 30, 200) — pick them out by the magenta channel test.
    if img.ndim != 3 or img.shape[2] < 3:
        return None
    bgr = img[..., :3]
    # BGR magenta = (200, 30, 200)
    mask_native = (
        (bgr[..., 0] > 150) & (bgr[..., 1] < 80) & (bgr[..., 2] > 150)
    ).astype(np.uint8) * 255
    if mask_native.max() == 0:
        return None
    h_target = max(int(h_orig * factor), 1)
    w_target = max(int(w_orig * factor), 1)
    if mask_native.shape != (h_target, w_target):
        # cv2.resize's output dtype can differ from the input's — cast
        # back to uint8 so the return type stays consistent with the
        # dtype declared in the signature.
        resized = cv2.resize(
            mask_native, (w_target, h_target), interpolation=cv2.INTER_NEAREST
        )
        return resized.astype(np.uint8)
    return mask_native


def compute_co2_diffusion(
    segformer_result: dict[str, Any],
    *,
    co2_morphometrics_result: dict[str, Any] | None = None,
    um_per_px: float | None = None,
    max_side_px: int = DEFAULT_MAX_SIDE_PX,
    ci_pa: float = DEFAULT_CI_PA,
    reaction_rate: float = DEFAULT_REACTION_RATE,
    diffusivity_override: dict[str, float] | None = None,
    kinetics_mode: str = DEFAULT_KINETICS_MODE,
    vcmax_per_volume_mol_m3_s: float = DEFAULT_VCMAX_PER_VOLUME,
    kc_pa: float = DEFAULT_KC_PA,
    ko_pa: float = DEFAULT_KO_PA,
    o2_pa: float = DEFAULT_O2_PA,
    gamma_star_pa: float = DEFAULT_GAMMA_STAR_PA,
    picard_max_iter: int = DEFAULT_PICARD_MAX_ITER,
    picard_tol_pa: float = DEFAULT_PICARD_TOL_PA,
) -> Co2DiffusionResult:
    """Solve the steady-state reaction-diffusion equation for CO2 inside
    the leaf.  See module docstring for the math.

    Parameters
    ----------
    segformer_result
        The `result` blob of a completed segformer_tissue analysis.
    co2_morphometrics_result
        Optional `result` blob from co2_morphometrics — when present we
        decode its chloroplast overlay and use those pixels as the
        reaction sink.  Otherwise falls back to mesophyll cells
        (palisade + spongy).
    um_per_px, max_side_px
        Same role as in Darcy: scale factor for absolute units, grid
        cap for solver tractability.
    ci_pa
        Stomatal Dirichlet value (CO2 partial pressure, Pa).  Default
        25 Pa (~250 ppm at 1 atm).
    kinetics_mode
        ``"michaelis_menten"`` (default) — Farquhar–von Caemmerer–
        Berry Rubisco-limited carboxylation, solved via Picard
        iteration.  ``"linear"`` — first-order ``R(C) = r · C`` (PR
        #13a legacy).
    vcmax_per_volume_mol_m3_s
        Volumetric carboxylation capacity inside the chloroplast
        sink region (M-M mode only).  See ``DEFAULT_VCMAX_PER_VOLUME``
        comment for typical values and the conversion from area-based
        ``V_cmax``.
    kc_pa, ko_pa, o2_pa, gamma_star_pa
        Bernacchi 2001 Rubisco kinetics constants at 25 °C (M-M mode
        only).  Override per-leaf if ``T_leaf != 25``; the standard
        Arrhenius corrections live in ``pipeline/farquhar.py`` and can
        be applied by the caller.
    picard_max_iter, picard_tol_pa
        Nonlinear iteration controls (M-M mode only).  Convergence
        criterion is ``max |C^(k) − C^(k−1)| < tol`` over the leaf
        interior.
    reaction_rate
        First-order rate r (1/s) used in linear mode only.
    diffusivity_override
        Per-class D overrides (m^2/s).  Non-finite/non-positive
        values are dropped silently.
    """
    # Reuse water_path's polygon -> mask helper as the single source
    # of truth for class rasterisation.
    from app.pipeline.water_path import _rasterise_class

    if not isinstance(segformer_result, dict):
        raise ValueError("segformer_result must be a dict")
    polygons = cast(list[dict[str, Any]], segformer_result.get("polygons") or [])
    shape = segformer_result.get("image_shape") or {}
    h_orig = int(shape.get("height_px") or 0)
    w_orig = int(shape.get("width_px") or 0)
    if h_orig <= 0 or w_orig <= 0:
        raise ValueError("segformer_result lacks usable image_shape")
    if not np.isfinite(ci_pa) or ci_pa <= 0:
        raise ValueError("ci_pa must be a positive finite number")
    if kinetics_mode not in KINETICS_MODES:
        raise ValueError(
            f"kinetics_mode must be one of {KINETICS_MODES}, got {kinetics_mode!r}"
        )
    if kinetics_mode == "linear":
        if not np.isfinite(reaction_rate) or reaction_rate < 0:
            raise ValueError("reaction_rate must be a non-negative finite number")
    else:  # michaelis_menten
        for name, val in (
            ("vcmax_per_volume_mol_m3_s", vcmax_per_volume_mol_m3_s),
            ("kc_pa", kc_pa),
            ("ko_pa", ko_pa),
            ("o2_pa", o2_pa),
            ("gamma_star_pa", gamma_star_pa),
        ):
            if not np.isfinite(val) or val < 0:
                raise ValueError(f"{name} must be a non-negative finite number")
        if kc_pa <= 0 or ko_pa <= 0:
            raise ValueError("kc_pa and ko_pa must be strictly positive")
        if picard_max_iter < 1 or not np.isfinite(picard_tol_pa) or picard_tol_pa <= 0:
            raise ValueError(
                "picard_max_iter must be >= 1 and picard_tol_pa must be positive"
            )

    longest = max(h_orig, w_orig)
    factor = max_side_px / longest if longest > max_side_px else 1.0
    h = max(int(h_orig * factor), 1)
    w = max(int(w_orig * factor), 1)
    inv_factor = 1.0 / factor
    dx_m = (inv_factor * (um_per_px or 1.0)) * 1e-6

    # ----- diffusivity field ----------------------------------------
    sanitised_override = _sanitise_overrides(diffusivity_override)
    diffusivity: dict[str, float] = {**DEFAULT_DIFFUSIVITY, **sanitised_override}
    d_field = np.full((h, w), BACKGROUND_DIFFUSIVITY, dtype=np.float64)
    leaf_mask = np.zeros((h, w), dtype=np.uint8)
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
        d_class = diffusivity.get(cls_key, BACKGROUND_DIFFUSIVITY)
        d_field = np.where(cls_mask > 0, d_class, d_field)
        leaf_mask = np.maximum(leaf_mask, cls_mask)

    # ----- sink (chloroplast) mask + sink_class label ---------------
    chloroplast_mask = _decode_chloroplast_mask(
        co2_morphometrics_result, h_orig, w_orig, factor
    )
    if chloroplast_mask is not None and chloroplast_mask.max() > 0:
        sink_class = "chloroplast"
        sink_mask = chloroplast_mask
    else:
        sink_class = "mesophyll_cells"
        sink_mask = np.zeros((h, w), dtype=np.uint8)
        for cls_key in FALLBACK_CHLOROPLAST_CLASSES:
            sink_mask = np.maximum(
                sink_mask, _rasterise_class(polygons, cls_key, h, w, factor)
            )
    if sink_mask.max() == 0:
        raise ValueError(
            "no chloroplast or mesophyll cells found in the SegFormer result; "
            "CO2 diffusion needs a sink region (Rubisco-bearing tissue)"
        )

    # ----- stomata Dirichlet boundary -------------------------------
    stomata_mask = _rasterise_class(polygons, "stomata", h, w, factor)
    if stomata_mask.max() == 0:
        raise ValueError(
            "no stomata polygons in the SegFormer result; CO2 needs an "
            "atmospheric inlet (Dirichlet boundary)"
        )

    # ----- assemble Laplacian (mode-independent) --------------------
    n = h * w

    def _harmonic(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        denom = a + b
        with np.errstate(divide="ignore", invalid="ignore"):
            res = np.where(denom > 0, 2.0 * a * b / denom, 0.0)
        return res

    dx = _harmonic(d_field[:, :-1], d_field[:, 1:])
    dy = _harmonic(d_field[:-1, :], d_field[1:, :])
    idx_grid = np.arange(n).reshape(h, w)

    base_rows: list[np.ndarray] = []
    base_cols: list[np.ndarray] = []
    base_data: list[np.ndarray] = []

    # East-west faces.
    center_e = idx_grid[:, :-1].ravel()
    right_w = idx_grid[:, 1:].ravel()
    dx_flat = dx.ravel()
    base_rows.extend([center_e, center_e, right_w, right_w])
    base_cols.extend([right_w, center_e, center_e, right_w])
    base_data.extend([dx_flat, -dx_flat, dx_flat, -dx_flat])

    # North-south faces.
    center_n = idx_grid[:-1, :].ravel()
    below_n = idx_grid[1:, :].ravel()
    dy_flat = dy.ravel()
    base_rows.extend([center_n, center_n, below_n, below_n])
    base_cols.extend([below_n, center_n, center_n, below_n])
    base_data.extend([dy_flat, -dy_flat, dy_flat, -dy_flat])

    base_rows_arr = np.concatenate(base_rows)
    base_cols_arr = np.concatenate(base_cols)
    base_data_arr = np.concatenate(base_data)
    laplacian = sparse.csr_matrix(
        (base_data_arr, (base_rows_arr, base_cols_arr)), shape=(n, n)
    )

    sink_bool = sink_mask > 0
    sink_idx = idx_grid[sink_bool].ravel()
    dx_sq = dx_m * dx_m

    dirichlet = stomata_mask > 0
    dirichlet_flat = dirichlet.ravel()
    dirichlet_idx = np.where(dirichlet_flat)[0]

    def _apply_reaction_and_solve(b_diag: np.ndarray, a_const: np.ndarray) -> np.ndarray:
        """Take the constant Laplacian and add the linearised reaction
        ``-b · C = a`` (per voxel, scaled by dx²) to it, then pin
        Dirichlet rows and solve.  ``b_diag`` and ``a_const`` are
        sized over the sink cells; the rest of the leaf has zero
        reaction contribution.
        """
        mat_lil = laplacian.tolil(copy=True)
        if sink_idx.size > 0:
            for k, i in enumerate(sink_idx):
                # Laplacian diagonal entry at [i, i] is already present
                # from the face-flux assembly; add the reaction
                # contribution -b * dx² (b already includes the sign
                # convention so that R increases C consumption).
                mat_lil[i, i] += -float(b_diag[k]) * dx_sq
        rhs_local = np.zeros(n, dtype=np.float64)
        if sink_idx.size > 0:
            # Constant part of the reaction goes to the RHS with the
            # opposite sign (we moved -bC and +a to the LHS; +a goes
            # to the RHS as -a after rearrangement).
            rhs_local[sink_idx] += float(dx_sq) * a_const
        for i in dirichlet_idx:
            mat_lil.rows[i] = [i]
            mat_lil.data[i] = [1.0]
            rhs_local[i] = ci_pa
        try:
            solved = spsolve(mat_lil.tocsc(), rhs_local)
        except Exception as exc:
            raise RuntimeError(f"CO2 diffusion solve failed: {exc}") from exc
        # spsolve returns Any per scipy stubs; cast to a typed ndarray.
        return np.asarray(solved, dtype=np.float64)

    picard_iterations = 0
    picard_residual_pa = 0.0
    if kinetics_mode == "linear":
        # R(C) = r·C, so b = r everywhere in the sink and a = 0.
        b_diag = np.full_like(sink_idx, reaction_rate, dtype=np.float64)
        a_const = np.zeros_like(sink_idx, dtype=np.float64)
        c_flat = _apply_reaction_and_solve(b_diag, a_const)
    else:
        # Michaelis-Menten via Picard iteration.  R(C) = V (C-Γ*)/(C+K_eff)
        # with K_eff = K_c (1 + O/K_o); R'(C) = V (K_eff + Γ*)/(C+K_eff)².
        # Initialize the chloroplast concentration from a one-shot
        # linear solve at r = V/(2 K_eff) so the first Picard step
        # already starts inside the convex regime; this halves the
        # iteration count compared with a flat C^0 = Ci start.
        k_eff = kc_pa * (1.0 + o2_pa / ko_pa)
        v_max = vcmax_per_volume_mol_m3_s
        if v_max <= 0:
            # No reaction → solution is C ≡ Ci everywhere.
            b_diag = np.zeros_like(sink_idx, dtype=np.float64)
            a_const = np.zeros_like(sink_idx, dtype=np.float64)
            c_flat = _apply_reaction_and_solve(b_diag, a_const)
        else:
            b_seed = np.full_like(sink_idx, v_max / (2.0 * k_eff), dtype=np.float64)
            a_seed = np.zeros_like(sink_idx, dtype=np.float64)
            c_flat = _apply_reaction_and_solve(b_seed, a_seed)
            for it in range(1, picard_max_iter + 1):
                # Linearize R around the previous iterate at sink cells.
                # Clip negative C to 0 inside the linearization to keep
                # the denominator (C+K_eff) ≥ K_eff; the post-solve
                # field still records the raw values for diagnostics.
                c_prev_sink = np.clip(c_flat[sink_idx], 0.0, None)
                denom = c_prev_sink + k_eff
                # b = R'(C_prev) = V (K_eff + Γ*) / (C_prev + K_eff)²
                b_iter = v_max * (k_eff + gamma_star_pa) / (denom * denom)
                # R(C_prev) = V (C_prev - Γ*) / (C_prev + K_eff)
                r_prev = v_max * (c_prev_sink - gamma_star_pa) / denom
                # a = R(C_prev) − b · C_prev (constant part of the
                # linearisation).
                a_iter = r_prev - b_iter * c_prev_sink
                c_new = _apply_reaction_and_solve(b_iter, a_iter)
                resid = float(np.max(np.abs(c_new - c_flat)))
                c_flat = c_new
                picard_iterations = it
                picard_residual_pa = resid
                if resid < picard_tol_pa:
                    break
            else:
                # Loop ran the full max_iter without breaking — we'll
                # log a note below.  c_flat holds the last iterate.
                pass
    concentration = c_flat.reshape(h, w)

    # Track solver quality indicators so a silently-unstable solve
    # surfaces in the `notes` field instead of hiding behind plausible
    # heatmaps (codex round-1 MINOR — non-finite / negative leakage).
    n_non_finite = int((~np.isfinite(concentration)).sum())
    concentration = np.where(np.isfinite(concentration), concentration, ci_pa)
    min_raw = float(concentration.min())
    n_negative = int((concentration < 0).sum())
    # Clip negative concentrations.  Small overshoots from sharp BCs
    # land at 0 here so downstream stats stay sensible; sizeable
    # negatives get flagged via the notes list below.
    concentration = np.clip(concentration, 0.0, None)

    # ----- aggregates -----------------------------------------------
    interior_leaf = (leaf_mask > 0) & (~dirichlet)
    sink_interior = sink_bool & interior_leaf

    cc_mean: float | None = (
        float(concentration[sink_interior].mean())
        if sink_interior.any()
        else None
    )

    leaf_finite = concentration[leaf_mask > 0]
    drawdown_mean: float | None
    drawdown_max: float | None
    if leaf_finite.size > 0:
        drawdown = ci_pa - leaf_finite
        drawdown_mean = float(drawdown.mean())
        drawdown_max = float(drawdown.max())
    else:
        drawdown_mean = None
        drawdown_max = None

    # ----- A_net = total CO2 flux INTO the chloroplast region -------
    def _boundary_outflow(mask_bool: np.ndarray) -> float:
        """Signed face flux LEAVING `mask_bool` across faces that
        separate an in-mask cell from a non-mask non-Dirichlet leaf
        cell.  The explicit `~m_other` gate handles the case where
        the mask IS part of the interior leaf (e.g. mesophyll cells
        as the CO2 sink) — without it, palisade↔palisade interior
        faces would both satisfy `m_left & int_right` and double-count
        the Laplacian contribution.  Darcy could use the simpler
        form because its masks (xylem / stomata) are always Dirichlet
        subsets and therefore disjoint from `interior_leaf`.
        """
        total = 0.0
        int_left = interior_leaf[:, :-1]
        int_right = interior_leaf[:, 1:]
        m_left = mask_bool[:, :-1]
        m_right = mask_bool[:, 1:]
        ll = m_left & int_right & (~m_right)
        if ll.any():
            total += float(
                np.sum(
                    dx[ll]
                    * (concentration[:, :-1][ll] - concentration[:, 1:][ll])
                )
            )
        rr = m_right & int_left & (~m_left)
        if rr.any():
            total += float(
                np.sum(
                    dx[rr]
                    * (concentration[:, 1:][rr] - concentration[:, :-1][rr])
                )
            )
        int_top = interior_leaf[:-1, :]
        int_bot = interior_leaf[1:, :]
        m_top = mask_bool[:-1, :]
        m_bot = mask_bool[1:, :]
        tt = m_top & int_bot & (~m_bot)
        if tt.any():
            total += float(
                np.sum(
                    dy[tt]
                    * (concentration[:-1, :][tt] - concentration[1:, :][tt])
                )
            )
        bb = m_bot & int_top & (~m_top)
        if bb.any():
            total += float(
                np.sum(
                    dy[bb]
                    * (concentration[1:, :][bb] - concentration[:-1, :][bb])
                )
            )
        return total

    # CO2 flowing INTO the sink region = − signed flux LEAVING it.
    # Units: mol / (s · m-depth), since dx_m in the face-flux formula
    # cancels the discrete gradient denominator against the face area
    # in 2-D extruded 1 metre into depth.  Use `sink_interior` (sink
    # cells explicitly excluding any Dirichlet overlap) so a
    # pathological chloroplast overlay that clips a stomatum pixel
    # doesn't distort A_net by adding a Dirichlet cell to the mask.
    a_net = -_boundary_outflow(sink_interior)

    # ----- volumetric reaction integral (per kinetics mode) ---------
    # SIGNED integral of R(C) over the sink (mol / s / m-depth) — this
    # is the quantity that closes against the boundary supply via
    # mass conservation.  Negative R contributions (where C < Γ* in
    # the M-M form) are kept here because the boundary face flux
    # integration sees the net flow including any local release; if
    # we clipped, the imbalance check would falsely flag every leaf
    # whose Ci sits near the compensation point.
    sink_c = concentration[sink_interior]
    if sink_interior.any():
        if kinetics_mode == "linear":
            r_field = reaction_rate * sink_c
        else:  # michaelis_menten — recompute R(C_final) without linearization
            k_eff = kc_pa * (1.0 + o2_pa / ko_pa)
            r_field = (
                vcmax_per_volume_mol_m3_s
                * (sink_c - gamma_star_pa)
                / (sink_c + k_eff)
            )
        reaction_volume_integral = float(r_field.sum()) * dx_m * dx_m
    else:
        reaction_volume_integral = 0.0

    # Conservation cross-check.  In steady state, three quantities are
    # numerically identical (divergence theorem on the leaf interior):
    #
    #     stomata_supply  = a_net  = ∫R(C) dV
    #
    # where stomata_supply is the flux LEAVING the Dirichlet stomata
    # face into the leaf, a_net is the flux INTO the sink across its
    # interior boundary, and ∫R(C) dV is the volumetric consumption
    # inside the sink.  Pre-PR #19 the old code asserted
    # `stomata_supply == a_net + ∫R` which double-counts; in steady
    # state for a single sink that's a 2× discrepancy.  The fix is to
    # check the actual physical equation: `stomata_supply == ∫R(C) dV`.
    #
    # Allow up to 5 % imbalance before flagging — sharp gas/liquid
    # diffusivity ratios (1.6e-5 vs 1.79e-9) introduce harmonic-mean
    # face-conductivity error of a few percent on coarser grids,
    # which is not a real conservation violation.
    stomata_supply = _boundary_outflow(dirichlet)  # flux LEAVING stomata = supply
    notes: list[str] = []
    if stomata_supply > 0:
        imbalance = abs(stomata_supply - reaction_volume_integral) / stomata_supply
        if imbalance > 0.05:
            notes.append(
                f"stomata supply vs ∫R(C) imbalance = {imbalance:.2%}; "
                "expected < 5 % after Picard convergence.  Check the "
                "chloroplast mask covers all consuming cells or "
                "increase max_side_px / picard_max_iter."
            )
    if n_non_finite > 0:
        notes.append(
            f"solver produced {n_non_finite} non-finite pixels; replaced "
            "with Ci.  This usually means the matrix was near-singular — "
            "check that stomata are not isolated from the leaf interior."
        )
    if n_negative > 0 or min_raw < -1e-3:
        notes.append(
            f"solver produced {n_negative} negative pixels (min={min_raw:.3e} Pa); "
            "clipped to 0.  Small overshoots near sharp BCs are normal; large "
            "negatives suggest the V_cmax / reaction rate is too strong for the grid."
        )
    if (
        kinetics_mode == "michaelis_menten"
        and picard_iterations >= picard_max_iter
        and picard_residual_pa >= picard_tol_pa
    ):
        notes.append(
            f"Picard iteration hit max_iter={picard_max_iter} without "
            f"reaching tol={picard_tol_pa:.1e} Pa "
            f"(final residual={picard_residual_pa:.3e} Pa).  Cc/A_net are "
            "approximate; raise picard_max_iter or coarsen V_cmax."
        )

    # g_m_proxy in standard area-normalised units:
    #     g_m_proxy = A_net / (leaf_section_length · (Ci - Cc))
    #               [mol / (s · m)]    /    (m · Pa)
    #               = mol / (m^2 · s · Pa)
    # matches the textbook g_m definition.  Round-1 review caught that
    # the un-normalised form A_net/(Ci - Cc) had units mol/(s·m·Pa),
    # which is NOT g_m — needed cross-section-length normalisation.
    leaf_section_length_m_val = _leaf_section_length_m(leaf_mask, dx_m)
    if (
        cc_mean is not None
        and (ci_pa - cc_mean) > 0
        and a_net > 0
        and leaf_section_length_m_val > 0
    ):
        g_m_proxy: float | None = a_net / (leaf_section_length_m_val * (ci_pa - cc_mean))
    else:
        g_m_proxy = None
        if cc_mean is not None and (ci_pa - cc_mean) <= 0:
            notes.append(
                "Cc mean >= Ci — gradient reversed.  Likely cause: reaction "
                "rate too small relative to grid scale, or sink region "
                "disconnected from stomata."
            )

    # ----- per-stomatum drawdown ------------------------------------
    stomata_polygons = [p for p in polygons if p.get("class_key") == "stomata"]
    per_stomatum: list[StomatumDrawdown] = []
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
        # Outer ring intersected with the non-Dirichlet leaf interior
        # — average concentration there to get the local "after-stomata"
        # value that defines the immediate drawdown the stomatum sees.
        single_outer = np.zeros((h, w), dtype=bool)
        single_outer[:-1, :] |= single_bool[1:, :]
        single_outer[1:, :] |= single_bool[:-1, :]
        single_outer[:, :-1] |= single_bool[:, 1:]
        single_outer[:, 1:] |= single_bool[:, :-1]
        single_outer &= ~single_bool
        single_outer &= interior_leaf
        if not single_outer.any():
            per_stomatum.append(
                StomatumDrawdown(
                    centroid=[cx_orig, cy_orig],
                    cc_mean_pa=None,
                    drawdown_pa=None,
                    flow_in=0.0,
                )
            )
            continue
        cc_local = float(concentration[single_outer].mean())
        # `_boundary_outflow(single_bool)` with single_bool being the
        # Dirichlet stomatum mask returns the SIGNED flux LEAVING the
        # stomatum.  In normal photosynthesis the gradient points from
        # stomata (C=Ci, high) into leaf (C<Ci, low), so flux leaves
        # the stomatum → positive.  `flow_in` names the same quantity
        # from the LEAF's frame of reference: CO2 flowing INTO the
        # leaf via this stomatum.  They have the same sign and same
        # magnitude, so we report the raw outflow directly (no negation
        # — the round-1 nit was that the previous code negated it but
        # the comment described it as supply leaving the stomatum).
        per_stomatum.append(
            StomatumDrawdown(
                centroid=[cx_orig, cy_orig],
                cc_mean_pa=cc_local,
                drawdown_pa=ci_pa - cc_local,
                flow_in=_boundary_outflow(single_bool),
            )
        )

    # ----- heatmaps -------------------------------------------------
    concentration_png = _heatmap_to_png_base64(concentration, alpha_mask=leaf_mask)
    drawdown_field = ci_pa - concentration
    drawdown_field = np.where(np.isfinite(drawdown_field), drawdown_field, 0.0)
    drawdown_png = _heatmap_to_png_base64(drawdown_field, alpha_mask=leaf_mask)

    if sink_class == "mesophyll_cells":
        notes.append(
            "co2_morphometrics not provided; using mesophyll cells "
            "(palisade + spongy) as the chloroplast sink approximation"
        )

    return Co2DiffusionResult(
        sink_class=sink_class,
        ci_pa=ci_pa,
        cc_mean_pa=cc_mean,
        drawdown_mean_pa=drawdown_mean,
        drawdown_max_pa=drawdown_max,
        a_net=a_net,
        leaf_section_length_m=leaf_section_length_m_val,
        g_m_proxy=g_m_proxy,
        kinetics_mode=kinetics_mode,
        vcmax_per_volume_mol_m3_s=vcmax_per_volume_mol_m3_s,
        kc_pa=kc_pa,
        ko_pa=ko_pa,
        o2_pa=o2_pa,
        gamma_star_pa=gamma_star_pa,
        picard_iterations=picard_iterations,
        picard_residual_pa=picard_residual_pa,
        stomata_drawdowns=per_stomatum,
        concentration_png_base64=concentration_png,
        drawdown_png_base64=drawdown_png,
        heatmap_shape=(h, w),
        downsample_factor=float(factor),
        diffusivity=diffusivity,
        reaction_rate=reaction_rate,
        notes=notes,
    )
