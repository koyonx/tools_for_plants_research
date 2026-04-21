"""SegFormer inference for multi-class tissue segmentation.

This runs a user-trained SegFormer checkpoint (produced by
`notebooks/segformer_train.ipynb` over a `training/export.zip`) and
returns per-class polygons + coverage statistics.  Heavy imports are
deferred so the rest of the backend still runs on images without the
`ml` extra installed.

Expected checkpoint layout at `PLANTS_SEGFORMER_DIR`:

    config.json
    model.safetensors            # or pytorch_model.bin
    preprocessor_config.json

If the directory is missing, `detect_tissue()` raises
`ModelNotFoundError`, which `api/segformer.py` turns into a 503.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.pipeline.classes import TISSUE_CLASS_BY_KEY, TISSUE_CLASS_KEYS
from app.pipeline.rasterize import CLASS_INDEX

DEFAULT_MODEL_DIR = os.environ.get("PLANTS_SEGFORMER_DIR", "/models/segformer")
DEFAULT_MAX_SIDE_PX = 1024
# Contours below this area (in down-sampled pixels) are dropped — they
# tend to be stray single-pixel labels from argmax thresholding.
MIN_CONTOUR_AREA_DS_PX = 12


class ModelNotFoundError(RuntimeError):
    """Raised when the configured checkpoint directory doesn't exist."""


@dataclass(frozen=True)
class TissuePolygon:
    class_key: str
    polygon: list[list[float]]
    area_px: int
    # Inner rings (holes) within the outer `polygon`.  Frontend renders
    # this as an SVG `<path>` with `fill-rule="evenodd"` so enclosed
    # classes (e.g. xylem inside a bundle-sheath ring) aren't covered
    # by the outer polygon's translucent fill.
    holes: list[list[list[float]]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ClassCoverage:
    class_key: str
    pixel_count: int
    area_px: int  # same value — kept for parity with um² scaling on the client
    coverage_ratio: float  # fraction of the image area

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SegFormerResult:
    model_dir: str
    classes: list[str]
    coverage: list[ClassCoverage]
    polygons: list[TissuePolygon] = field(default_factory=list)
    downsample_factor: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_dir": self.model_dir,
            "classes": self.classes,
            "coverage": [c.to_dict() for c in self.coverage],
            "polygons": [p.to_dict() for p in self.polygons],
            "downsample_factor": self.downsample_factor,
        }


_loaded_model: dict[str, Any] = {}


def _load_model(model_dir: str) -> tuple[Any, Any]:
    cached = _loaded_model.get(model_dir)
    if cached is not None:
        return cached["model"], cached["processor"]

    if not Path(model_dir).exists():
        raise ModelNotFoundError(
            f"SegFormer checkpoint not found at {model_dir}. See models/README.md."
        )

    # Heavy imports deferred so the rest of the pipeline stays importable
    # on machines without the ml extras installed (e.g. CI lint).
    from transformers import SegformerForSemanticSegmentation, SegformerImageProcessor

    processor = SegformerImageProcessor.from_pretrained(model_dir)
    model = SegformerForSemanticSegmentation.from_pretrained(model_dir)
    model.eval()
    _loaded_model[model_dir] = {"model": model, "processor": processor}
    return model, processor


def _label_index_to_key(idx: int) -> str | None:
    """Map the model's output index back to our tissue-class key.

    The trainer stores `id2label` in the checkpoint config; we fall back
    to our own `CLASS_INDEX` (`rasterize.py`, 1-based with 0 = background)
    if the config doesn't cover a particular label.
    """
    if idx == 0:
        return None  # background
    if 1 <= idx <= len(TISSUE_CLASS_KEYS):
        return TISSUE_CLASS_KEYS[idx - 1]
    return None


def detect_tissue(
    image_bgr: np.ndarray,
    *,
    max_side_px: int = DEFAULT_MAX_SIDE_PX,
    model_dir: str = DEFAULT_MODEL_DIR,
) -> SegFormerResult:
    """Run the fine-tuned SegFormer and summarise per-class coverage + polygons.

    The returned polygons are expressed in the original image's pixel
    grid, not the down-sampled inference grid.
    """
    if image_bgr.ndim != 3 or image_bgr.shape[2] != 3:
        raise ValueError("image_bgr must be an HxWx3 BGR array")

    model, processor = _load_model(model_dir)

    h, w = image_bgr.shape[:2]
    longest = max(h, w)
    factor = max_side_px / longest if longest > max_side_px else 1.0
    if factor < 1.0:
        resized = cv2.resize(
            image_bgr,
            (int(w * factor), int(h * factor)),
            interpolation=cv2.INTER_AREA,
        )
    else:
        resized = image_bgr
    inv_factor = 1.0 / factor

    # Heavy imports deferred (same reason as _load_model)
    import torch

    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    with torch.no_grad():
        inputs = processor(images=rgb, return_tensors="pt")
        outputs = model(**inputs)
        # Upsample logits to the RGB-sized grid so argmax coordinates line
        # up with the resized input exactly.
        logits = torch.nn.functional.interpolate(
            outputs.logits,
            size=rgb.shape[:2],
            mode="bilinear",
            align_corners=False,
        )
        pred = logits.argmax(dim=1)[0].cpu().numpy().astype(np.uint8)

    # Map model indices through id2label when present (labels can be
    # reordered during training), else use the default 1-based ordering
    # shared with the rasteriser.
    id2key: dict[int, str | None] = {}
    id2label = getattr(getattr(model, "config", None), "id2label", None)
    for i in range(int(pred.max()) + 1):
        label = id2label.get(i) if isinstance(id2label, dict) else None
        if isinstance(label, str) and label in TISSUE_CLASS_BY_KEY:
            id2key[i] = label
        else:
            id2key[i] = _label_index_to_key(i)

    total_px_ds = int(pred.size)
    coverage_rows: list[ClassCoverage] = []
    polygon_rows: list[TissuePolygon] = []

    # Class indices observed in the prediction
    observed = [int(v) for v in np.unique(pred) if int(v) != 0]
    for label_idx in observed:
        key = id2key.get(label_idx)
        if key is None:
            continue
        class_mask = (pred == label_idx).astype(np.uint8)
        pixel_count_ds = int(class_mask.sum())
        if pixel_count_ds == 0:
            continue

        # Coverage is reported against the original pixel grid so area
        # numbers stay comparable across runs at different max_side_px.
        pixel_count = int(pixel_count_ds * inv_factor * inv_factor)
        coverage_rows.append(
            ClassCoverage(
                class_key=key,
                pixel_count=pixel_count,
                area_px=pixel_count,
                coverage_ratio=pixel_count_ds / total_px_ds,
            )
        )

        # RETR_CCOMP gives a 2-level hierarchy: outer contours at the top,
        # holes (where another class punches through) as their children.
        # We keep the outers and attach their children as `holes`, so a
        # bundle-sheath ring around xylem doesn't paint a solid disk over
        # the xylem polygon at render time.
        contours, hierarchy = cv2.findContours(
            class_mask * 255, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE
        )

        def _approx(contour: np.ndarray) -> list[list[float]]:
            epsilon = 0.005 * cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, epsilon, True)
            return [
                [float(p[0][0]) * inv_factor, float(p[0][1]) * inv_factor] for p in approx
            ]

        # hierarchy is shape (1, N, 4): [next, prev, first_child, parent].
        h = hierarchy[0] if hierarchy is not None else []
        for i, contour in enumerate(contours):
            entry = h[i] if i < len(h) else None
            parent = int(entry[3]) if entry is not None else -1
            if parent != -1:
                # Skip child contours here — they're attached as holes
                # to whichever outer contour we'd already emit.
                continue
            area_ds = float(cv2.contourArea(contour))
            if area_ds < MIN_CONTOUR_AREA_DS_PX:
                continue
            outer = _approx(contour)
            if len(outer) < 3:
                continue

            holes: list[list[list[float]]] = []
            child_idx = int(entry[2]) if entry is not None else -1
            while child_idx != -1 and child_idx < len(contours):
                child = contours[child_idx]
                child_area_ds = float(cv2.contourArea(child))
                if child_area_ds >= MIN_CONTOUR_AREA_DS_PX:
                    hole = _approx(child)
                    if len(hole) >= 3:
                        holes.append(hole)
                child_idx = int(h[child_idx][0])  # next sibling

            polygon_rows.append(
                TissuePolygon(
                    class_key=key,
                    polygon=outer,
                    area_px=int(area_ds * inv_factor * inv_factor),
                    holes=holes,
                )
            )

    # Ensure every known class appears in the coverage list (with zero
    # counts if absent) so the UI can render a stable table.
    observed_keys = {c.class_key for c in coverage_rows}
    for key in TISSUE_CLASS_KEYS:
        if key not in observed_keys:
            coverage_rows.append(
                ClassCoverage(class_key=key, pixel_count=0, area_px=0, coverage_ratio=0.0)
            )
    coverage_rows.sort(key=lambda c: CLASS_INDEX.get(c.class_key, 99))

    return SegFormerResult(
        model_dir=model_dir,
        classes=list(TISSUE_CLASS_KEYS),
        coverage=coverage_rows,
        polygons=polygon_rows,
        downsample_factor=float(factor),
    )
