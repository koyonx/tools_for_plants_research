"""Tissue-class taxonomy shared with the frontend.

Kept in lock-step with frontend/lib/tissue-classes.ts.  Backend uses this
when rasterising polygon annotations into training masks for the
deep-learning pipeline landing in PR #5.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TissueClass:
    key: str
    label: str
    color: str  # hex


TISSUE_CLASSES: tuple[TissueClass, ...] = (
    TissueClass("upper_epidermis", "上側表皮", "#ef4444"),
    TissueClass("lower_epidermis", "下側表皮", "#f97316"),
    TissueClass("palisade", "柵状葉肉", "#eab308"),
    TissueClass("spongy", "海綿状葉肉", "#22c55e"),
    TissueClass("bundle_sheath", "維管束鞘", "#06b6d4"),
    TissueClass("xylem", "木部", "#3b82f6"),
    TissueClass("phloem", "師部", "#8b5cf6"),
    TissueClass("stomata", "気孔", "#ec4899"),
    TissueClass("intercellular", "細胞間隙", "#14b8a6"),
    TissueClass("other", "その他", "#6b7280"),
)

TISSUE_CLASS_KEYS: tuple[str, ...] = tuple(c.key for c in TISSUE_CLASSES)
TISSUE_CLASS_BY_KEY: dict[str, TissueClass] = {c.key: c for c in TISSUE_CLASSES}
