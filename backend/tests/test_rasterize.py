"""Synthetic tests for the polygon-to-mask rasteriser."""

from __future__ import annotations

import numpy as np

from app.pipeline.rasterize import CLASS_INDEX, classes_manifest, rasterize_annotations


def _triangle(x0: int, y0: int, x1: int, y1: int) -> list[list[int]]:
    # Clockwise triangle covering the bounding rectangle (x0..x1, y0..y1).
    return [[x0, y0], [x1, y0], [x0, y1]]


def test_empty_annotations_produces_zero_mask() -> None:
    mask = rasterize_annotations([], 50, 60)
    assert mask.shape == (50, 60)
    assert mask.dtype == np.uint8
    assert mask.max() == 0


def test_single_class_polygon_paints_expected_index() -> None:
    ann = [
        {"class": "palisade", "polygon": [[10, 10], [40, 10], [40, 40], [10, 40]]},
    ]
    mask = rasterize_annotations(ann, 100, 100)
    idx = CLASS_INDEX["palisade"]
    # interior pixel
    assert mask[20, 20] == idx
    # outside the polygon stays 0
    assert mask[5, 5] == 0
    # all non-zero pixels carry the target index, never a different one
    nonzero = np.unique(mask[mask > 0])
    assert nonzero.tolist() == [idx]


def test_overlapping_polygons_last_write_wins() -> None:
    # Two overlapping squares — second (spongy) should overwrite palisade.
    ann = [
        {"class": "palisade", "polygon": [[0, 0], [30, 0], [30, 30], [0, 30]]},
        {"class": "spongy", "polygon": [[15, 15], [45, 15], [45, 45], [15, 45]]},
    ]
    mask = rasterize_annotations(ann, 60, 60)
    # inside palisade-only region
    assert mask[5, 5] == CLASS_INDEX["palisade"]
    # overlap: spongy wins
    assert mask[20, 20] == CLASS_INDEX["spongy"]
    # inside spongy-only region
    assert mask[40, 40] == CLASS_INDEX["spongy"]


def test_unknown_class_is_skipped() -> None:
    ann = [
        {"class": "bogus", "polygon": [[0, 0], [10, 0], [10, 10]]},
        {"class": "xylem", "polygon": _triangle(20, 20, 40, 40)},
    ]
    mask = rasterize_annotations(ann, 50, 50)
    nonzero_vals = np.unique(mask[mask > 0])
    assert nonzero_vals.tolist() == [CLASS_INDEX["xylem"]]


def test_malformed_polygon_is_skipped() -> None:
    ann = [
        {"class": "palisade", "polygon": "not a list"},
        {"class": "palisade", "polygon": [[0, 0], [10, 0]]},  # < 3 pts
        {"class": "palisade", "polygon": [[0, 0], [10, 0], [5, 5]]},  # ok
    ]
    mask = rasterize_annotations(ann, 50, 50)
    assert mask.max() == CLASS_INDEX["palisade"]


def test_out_of_bounds_points_are_clipped() -> None:
    # Triangle mostly outside a 20-by-20 image — should still fill inside edges.
    ann = [
        {"class": "stomata", "polygon": [[-10, -10], [30, -10], [-10, 30]]},
    ]
    mask = rasterize_annotations(ann, 20, 20)
    # Due to clipping, the full mask becomes a triangle inside the frame.
    assert mask[5, 5] == CLASS_INDEX["stomata"]
    # origin is now on the (clipped) polygon interior
    assert mask[0, 0] == CLASS_INDEX["stomata"]


def test_rejects_invalid_dimensions() -> None:
    import pytest

    with pytest.raises(ValueError):
        rasterize_annotations([], 0, 10)
    with pytest.raises(ValueError):
        rasterize_annotations([], 10, -1)


def test_classes_manifest_shape() -> None:
    m = classes_manifest()
    assert m["background_index"] == 0
    indices = [entry["index"] for entry in m["classes"]]
    assert indices == sorted(indices)
    assert indices[0] == 1  # 1-based
    keys = {entry["key"] for entry in m["classes"]}
    assert "palisade" in keys
