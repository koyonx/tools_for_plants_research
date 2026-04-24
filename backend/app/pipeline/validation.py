"""Literature sanity-check validator for measured pipeline values.

Takes one or more analysis `result` blobs + the image's
photosynthesis_type and returns a list of `ValidationFinding` rows —
one per parameter that has both a measured value AND a literature
range on file.  Each finding is classified:

  - "within"  : measured value falls between the literature min and max
  - "below"   : measured value is below the literature min
  - "above"   : measured value is above the literature max
  - "unknown" : no literature range available for this parameter /
                photosynthesis_type combination

The classifier is pure: no DB access, no external state.  Endpoints
in `api/validation.py` orchestrate the data-fetching side.

Parameter keys match the `/compare` METRICS catalog so a finding's
`parameter_key` directly tells the UI which metric it applies to.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from app.api.compare import METRICS
from app.pipeline.literature_ranges import LiteratureRange, find_range


@dataclass(frozen=True)
class ValidationFinding:
    parameter_key: str
    measured: float | None
    status: str              # "within" | "below" | "above" | "unknown"
    range_min: float | None
    range_typical: float | None
    range_max: float | None
    unit: str
    source: str
    applies_to: str          # the photosynthesis_type the range was chosen for
    note: str
    analysis_kind: str       # which pipeline emitted `measured`

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ValidationReport:
    photosynthesis_type: str | None
    findings: list[ValidationFinding] = field(default_factory=list)

    @property
    def n_within(self) -> int:
        return sum(1 for f in self.findings if f.status == "within")

    @property
    def n_outside(self) -> int:
        return sum(1 for f in self.findings if f.status in ("below", "above"))

    @property
    def n_unknown(self) -> int:
        return sum(1 for f in self.findings if f.status == "unknown")

    def to_dict(self) -> dict[str, Any]:
        return {
            "photosynthesis_type": self.photosynthesis_type,
            "n_within": self.n_within,
            "n_outside": self.n_outside,
            "n_unknown": self.n_unknown,
            "findings": [f.to_dict() for f in self.findings],
        }


def _extract(value: Any, path: tuple[str, ...]) -> float | None:
    """Same dotted-path extractor as compare.py — duplicated here so
    the validator doesn't import from compare's endpoint layer (would
    make the FastAPI router an import-time dependency of a pure pipeline
    module).  If the compare module's `_extract` signature changes,
    keep this one in lock-step."""
    cur: Any = value
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
        if cur is None:
            return None
    try:
        v = float(cur)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _classify(
    measured: float,
    range_: LiteratureRange,
) -> str:
    if measured < range_.min:
        return "below"
    if measured > range_.max:
        return "above"
    return "within"


def validate_analyses(
    analyses_by_kind: dict[str, dict[str, Any]],
    photosynthesis_type: str | None,
) -> ValidationReport:
    """Given a `{kind: result_blob}` map and the image's
    photosynthesis_type, return a flat list of findings.

    We use compare.METRICS as the single source of truth for which
    scalar fields exist in each result blob (so new metrics added
    there automatically get validated when a literature range is
    registered).  Parameters without a literature range at all are
    skipped silently — the point is to validate what we can cite,
    not to flood the UI with unknowns.
    """
    findings: list[ValidationFinding] = []
    for metric in METRICS:
        result_blob = analyses_by_kind.get(metric.analysis_kind)
        if result_blob is None:
            continue
        measured = _extract(result_blob, metric.path)
        range_ = find_range(metric.key, photosynthesis_type)
        if range_ is None:
            # Only surface an "unknown" finding when we HAVE a
            # measurement AND the parameter is one we'd like to
            # validate (lookup returned None because the lit table
            # has no entry for this species).  If `measured` is
            # None too, skip silently — a row with both unknowns
            # would be pure clutter.
            if measured is None:
                continue
            findings.append(
                ValidationFinding(
                    parameter_key=metric.key,
                    measured=measured,
                    status="unknown",
                    range_min=None,
                    range_typical=None,
                    range_max=None,
                    unit=metric.unit,
                    source="",
                    applies_to=photosynthesis_type or "",
                    note="no literature range on file for this parameter / type",
                    analysis_kind=metric.analysis_kind,
                )
            )
            continue
        if measured is None:
            continue
        findings.append(
            ValidationFinding(
                parameter_key=metric.key,
                measured=measured,
                status=_classify(measured, range_),
                range_min=range_.min,
                range_typical=range_.typical,
                range_max=range_.max,
                unit=range_.unit,
                source=range_.source,
                applies_to=range_.applies_to,
                note=range_.note,
                analysis_kind=metric.analysis_kind,
            )
        )
    return ValidationReport(photosynthesis_type=photosynthesis_type, findings=findings)


def validate_gm_fit_result(
    gm_fit_result: dict[str, Any],
    photosynthesis_type: str | None,
) -> ValidationReport:
    """Per-method validation for the `GmFitResult` blob emitted by
    `pipeline/gm_fit.py`.  Each method row contributes up to 3
    findings (g_m, vcmax, j_max) depending on availability.

    The `parameter_key` is prefixed with `gm_fit.` so ranges in
    literature_ranges.py can cover these fields without colliding
    with the flat /compare METRICS namespace.
    """
    findings: list[ValidationFinding] = []
    methods = gm_fit_result.get("methods") if isinstance(gm_fit_result, dict) else None
    if not isinstance(methods, list):
        return ValidationReport(photosynthesis_type=photosynthesis_type, findings=findings)
    for method_row in methods:
        if not isinstance(method_row, dict):
            continue
        method_name = method_row.get("method", "unknown_method")
        for field_name in ("g_m", "vcmax", "j_max", "rd"):
            measured_raw = method_row.get(field_name)
            try:
                measured = float(measured_raw) if measured_raw is not None else None
            except (TypeError, ValueError):
                measured = None
            if measured is None or not math.isfinite(measured):
                continue
            param_key = f"gm_fit.{field_name}"
            range_ = find_range(param_key, photosynthesis_type)
            if range_ is None:
                findings.append(
                    ValidationFinding(
                        parameter_key=param_key,
                        measured=measured,
                        status="unknown",
                        range_min=None,
                        range_typical=None,
                        range_max=None,
                        unit="",
                        source="",
                        applies_to=photosynthesis_type or "",
                        note=f"no literature range for {param_key}",
                        analysis_kind=f"gm_fit:{method_name}",
                    )
                )
                continue
            findings.append(
                ValidationFinding(
                    parameter_key=param_key,
                    measured=measured,
                    status=_classify(measured, range_),
                    range_min=range_.min,
                    range_typical=range_.typical,
                    range_max=range_.max,
                    unit=range_.unit,
                    source=range_.source,
                    applies_to=range_.applies_to,
                    note=range_.note,
                    analysis_kind=f"gm_fit:{method_name}",
                )
            )
    return ValidationReport(photosynthesis_type=photosynthesis_type, findings=findings)
