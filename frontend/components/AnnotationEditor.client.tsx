"use client";

import { createClient } from "@/lib/supabase/client";
import type { AnnotationRow } from "@/lib/supabase/types";
import { TISSUE_CLASSES, TISSUE_CLASS_BY_KEY, type TissueClassKey } from "@/lib/tissue-classes";
import type Konva from "konva";
import type { KonvaEventObject } from "konva/lib/Node";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Circle, Image as KonvaImage, Layer, Line, Stage } from "react-konva";
import { errorMessage } from "@/lib/error-message";

export type AnnotationEditorProps = {
  imageId: string;
  imageUrl: string;
  imageWidth: number;
  imageHeight: number;
  initial: AnnotationRow[];
  currentUserId: string;
};

const STAGE_HEIGHT = 640;
const MIN_SCALE = 0.05;
const MAX_SCALE = 40;
const VERTEX_RADIUS_PX_VIEW = 5;

function pointerInStageCoords(stage: Konva.Stage): { x: number; y: number } | null {
  const pos = stage.getPointerPosition();
  if (!pos) return null;
  const t = stage.getAbsoluteTransform().copy();
  t.invert();
  return t.point(pos);
}

function hexToRgba(hex: string, alpha: number): string {
  const h = hex.replace("#", "");
  const r = Number.parseInt(h.slice(0, 2), 16);
  const g = Number.parseInt(h.slice(2, 4), 16);
  const b = Number.parseInt(h.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function flattenPoints(polygon: number[][]): number[] {
  const out: number[] = [];
  for (const p of polygon) {
    out.push(p[0], p[1]);
  }
  return out;
}

// Defensive in case the DB yields rows that pre-date the polygon CHECK
// constraint — the editor must never render a crash-inducing shape.
function isWellFormedPolygon(polygon: unknown): polygon is number[][] {
  if (!Array.isArray(polygon) || polygon.length < 3) return false;
  for (const pt of polygon) {
    if (!Array.isArray(pt) || pt.length !== 2) return false;
    if (typeof pt[0] !== "number" || typeof pt[1] !== "number") return false;
  }
  return true;
}

export function AnnotationEditorInner({
  imageId,
  imageUrl,
  imageWidth,
  imageHeight,
  initial,
  currentUserId,
}: AnnotationEditorProps) {
  const supabase = useMemo(() => createClient(), []);
  const stageRef = useRef<Konva.Stage | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  // Tracks whether we've already fit the image to the stage.  Without this,
  // any ResizeObserver firing (e.g. devtools toggled, sidebar opened) would
  // reset the user's pan/zoom to the centred fit.
  const didInitialFitRef = useRef(false);

  const [stageWidth, setStageWidth] = useState(960);
  const [image, setImage] = useState<HTMLImageElement | null>(null);

  const [annotations, setAnnotations] = useState<AnnotationRow[]>(initial);
  const [currentClass, setCurrentClass] = useState<TissueClassKey>("palisade");
  const [currentPolygon, setCurrentPolygon] = useState<number[][]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  // Hold Space to pan while a polygon is in progress; mirrors the
  // convention in most annotation tools (e.g. Label Studio / CVAT).
  const [panMode, setPanMode] = useState(false);

  // Edit-mode state for an existing annotation.  When `editingId` is set,
  // the canvas hides the new-polygon UI and instead shows draggable
  // vertex handles plus midpoint "+" markers for inserting a new point.
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingPolygon, setEditingPolygon] = useState<number[][]>([]);
  const [editingClass, setEditingClass] = useState<TissueClassKey>("palisade");

  // Responsive stage width
  useEffect(() => {
    if (!containerRef.current) return;
    const el = containerRef.current;
    const ro = new ResizeObserver(() => setStageWidth(el.clientWidth));
    ro.observe(el);
    setStageWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  // Load image (HTMLImageElement for Konva.Image).
  // No crossOrigin — we never call toDataURL, and signed Supabase URLs are
  // not guaranteed to ship CORS headers, so requesting CORS can leave the
  // image permanently in "loading" state.
  useEffect(() => {
    const img = new window.Image();
    img.src = imageUrl;
    img.onload = () => {
      setImage(img);
      setError((prev) => (prev === "画像の読み込みに失敗しました" ? null : prev));
    };
    img.onerror = () => setError("画像の読み込みに失敗しました");
  }, [imageUrl]);

  // Apply initial fit exactly once per image — otherwise every ResizeObserver
  // tick (opening DevTools, toggling a sidebar, etc.) would snap the stage
  // back and erase the user's pan/zoom.
  useEffect(() => {
    if (!stageRef.current || !image || didInitialFitRef.current) return;
    const stage = stageRef.current;
    const fit = Math.min(stageWidth / imageWidth, STAGE_HEIGHT / imageHeight);
    stage.scale({ x: fit, y: fit });
    stage.position({
      x: (stageWidth - imageWidth * fit) / 2,
      y: (STAGE_HEIGHT - imageHeight * fit) / 2,
    });
    stage.batchDraw();
    didInitialFitRef.current = true;
    // Focus the editor container so our scoped key handler fires.
    containerRef.current?.focus();
  }, [image, stageWidth, imageWidth, imageHeight]);

  const handleWheel = (e: KonvaEventObject<WheelEvent>) => {
    e.evt.preventDefault();
    const stage = e.target.getStage();
    if (!stage) return;
    const oldScale = stage.scaleX();
    const pointer = stage.getPointerPosition();
    if (!pointer) return;
    const mousePointTo = {
      x: (pointer.x - stage.x()) / oldScale,
      y: (pointer.y - stage.y()) / oldScale,
    };
    const direction = e.evt.deltaY > 0 ? -1 : 1;
    const factor = 1.1;
    let newScale = direction > 0 ? oldScale * factor : oldScale / factor;
    newScale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, newScale));
    stage.scale({ x: newScale, y: newScale });
    stage.position({
      x: pointer.x - mousePointTo.x * newScale,
      y: pointer.y - mousePointTo.y * newScale,
    });
    stage.batchDraw();
  };

  const handleStageClick = (e: KonvaEventObject<MouseEvent>) => {
    // While panning we never want a spurious click to add a vertex.
    if (panMode) return;
    // Editing an existing annotation locks out new-polygon drawing — the
    // user is dragging handles, not laying down fresh vertices.
    if (editingId) return;
    // Ignore clicks on existing annotations (we use Line onClick for edit)
    if (e.target !== e.target.getStage() && e.target.className !== "Image") {
      return;
    }
    const stage = e.target.getStage();
    if (!stage) return;
    const pos = pointerInStageCoords(stage);
    if (!pos) return;
    // Clicks outside the image (blank stage area, especially visible after
    // panning/zooming) must not land as polygon vertices — clamping to the
    // image rectangle keeps pixel coords legal for the training rasterizer.
    const x = Math.min(Math.max(pos.x, 0), imageWidth);
    const y = Math.min(Math.max(pos.y, 0), imageHeight);
    setCurrentPolygon((prev) => [...prev, [x, y]]);
  };

  const cancelCurrent = useCallback(() => setCurrentPolygon([]), []);

  const undoLast = useCallback(() => {
    setCurrentPolygon((prev) => prev.slice(0, -1));
  }, []);

  const savePolygon = useCallback(async () => {
    if (currentPolygon.length < 3) {
      setError("ポリゴンは 3 点以上必要です");
      return;
    }
    setError(null);
    setSaving(true);
    try {
      const { data, error: insErr } = await supabase
        .from("annotations")
        .insert({
          image_id: imageId,
          owner_id: currentUserId,
          class: currentClass,
          polygon: currentPolygon,
        })
        .select("*")
        .single<AnnotationRow>();
      if (insErr) throw insErr;
      if (data) {
        setAnnotations((prev) => [...prev, data]);
        setCurrentPolygon([]);
      }
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setSaving(false);
    }
  }, [currentPolygon, currentClass, imageId, currentUserId, supabase]);

  const deleteAnnotation = async (id: string) => {
    const { error: delErr } = await supabase.from("annotations").delete().eq("id", id);
    if (delErr) {
      setError(errorMessage(delErr));
      return;
    }
    setAnnotations((prev) => prev.filter((a) => a.id !== id));
    if (editingId === id) {
      setEditingId(null);
      setEditingPolygon([]);
    }
  };

  // ---- Edit-existing-annotation flow ----
  const startEditing = (a: AnnotationRow) => {
    if (a.owner_id !== currentUserId) return;
    if (!isWellFormedPolygon(a.polygon)) return;
    setError(null);
    setCurrentPolygon([]); // cancel any in-progress drawing
    setEditingId(a.id);
    setEditingPolygon(a.polygon.map((p) => [p[0], p[1]]));
    // Fall back to "other" if the row's class drifted out of the
    // frontend's known taxonomy (e.g. an old annotation predating a
    // class rename).  Casting blindly would let the editor save back
    // a class the DB CHECK now rejects.
    const known = TISSUE_CLASS_BY_KEY[a.class as TissueClassKey];
    setEditingClass(known ? (a.class as TissueClassKey) : "other");
  };

  const cancelEditing = useCallback(() => {
    setEditingId(null);
    setEditingPolygon([]);
  }, []);

  const moveEditingVertex = useCallback(
    (i: number, x: number, y: number) => {
      const cx = Math.min(Math.max(x, 0), imageWidth);
      const cy = Math.min(Math.max(y, 0), imageHeight);
      setEditingPolygon((prev) => prev.map((p, j) => (j === i ? [cx, cy] : p)));
    },
    [imageWidth, imageHeight],
  );

  const deleteEditingVertex = useCallback((i: number) => {
    setEditingPolygon((prev) => {
      if (prev.length <= 3) return prev; // need ≥3 to remain a polygon
      return prev.filter((_, j) => j !== i);
    });
  }, []);

  const insertEditingVertex = useCallback((afterIndex: number, x: number, y: number) => {
    setEditingPolygon((prev) => {
      const out = [...prev];
      out.splice(afterIndex + 1, 0, [x, y]);
      return out;
    });
  }, []);

  const saveEdits = useCallback(async () => {
    if (!editingId) return;
    if (editingPolygon.length < 3) {
      setError("ポリゴンは 3 点以上必要です");
      return;
    }
    setError(null);
    setSaving(true);
    try {
      const { data, error: updErr } = await supabase
        .from("annotations")
        .update({ polygon: editingPolygon, class: editingClass })
        .eq("id", editingId)
        .select("*")
        .single<AnnotationRow>();
      if (updErr) throw updErr;
      if (data) {
        setAnnotations((prev) => prev.map((a) => (a.id === editingId ? data : a)));
        setEditingId(null);
        setEditingPolygon([]);
      }
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setSaving(false);
    }
  }, [editingId, editingPolygon, editingClass, supabase]);

  const deleteEditingAnnotation = useCallback(async () => {
    if (!editingId) return;
    if (!window.confirm("このアノテーションを削除しますか？")) return;
    await deleteAnnotation(editingId);
  }, [editingId]); // deleteAnnotation closes over editingId itself

  // Keyboard shortcuts scoped to the editor container (tabIndex={0}).
  // Attaching via React's onKeyDown / onKeyUp on the container avoids
  // hijacking Space on the rest of the page (e.g. activating a button or
  // scrolling the viewport) the way a global `window` listener would.
  const onContainerKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const target = e.target as HTMLElement | null;
    if (target?.tagName === "INPUT" || target?.tagName === "TEXTAREA" || target?.tagName === "SELECT") return;
    if (e.code === "Space") {
      e.preventDefault();
      setPanMode(true);
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      if (editingId) void saveEdits();
      else void savePolygon();
    } else if (e.key === "Escape") {
      if (editingId) cancelEditing();
      else cancelCurrent();
    } else if (e.key === "Backspace") {
      // While editing we don't yank random vertices on Backspace — the
      // canvas has explicit per-vertex delete via Shift+click on a handle.
      if (!editingId) undoLast();
    }
  };

  const onContainerKeyUp = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.code === "Space") setPanMode(false);
  };

  // A window-level keyup guarantees we clear panMode even if focus leaves
  // the container while Space is held (e.g. Cmd-Tab on macOS eats keyup).
  useEffect(() => {
    const onWindowKeyUp = (e: KeyboardEvent) => {
      if (e.code === "Space") setPanMode(false);
    };
    const onWindowBlur = () => setPanMode(false);
    window.addEventListener("keyup", onWindowKeyUp);
    window.addEventListener("blur", onWindowBlur);
    return () => {
      window.removeEventListener("keyup", onWindowKeyUp);
      window.removeEventListener("blur", onWindowBlur);
    };
  }, []);

  const currentClassColor = TISSUE_CLASS_BY_KEY[currentClass]?.color ?? "#888";
  const editingClassColor = TISSUE_CLASS_BY_KEY[editingClass]?.color ?? "#888";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-neutral-500">クラス:</span>
        {TISSUE_CLASSES.map((c) => (
          <button
            key={c.key}
            type="button"
            onClick={() => setCurrentClass(c.key)}
            className={`flex items-center gap-1 rounded border px-2 py-1 ${
              currentClass === c.key
                ? "border-neutral-900 bg-neutral-100 dark:border-white dark:bg-neutral-800"
                : "border-neutral-300 dark:border-neutral-700"
            }`}
          >
            <span
              aria-hidden
              className="inline-block h-3 w-3 rounded-sm"
              style={{ backgroundColor: c.color }}
            />
            {c.label}
          </button>
        ))}
      </div>

      {/*
        tabIndex on this div is intentional: role="application" marks it as
        an interactive editor surface (WAI-ARIA), and focusing it lets our
        scoped onKeyDown handler capture Space / Enter / Backspace without
        hijacking the rest of the page.  Biome's noNoninteractiveTabindex
        rule is disabled project-wide for exactly this case (see biome.json).
      */}
      <div
        ref={containerRef}
        tabIndex={0}
        role="application"
        aria-label="ポリゴンアノテーションエディタ"
        onKeyDown={onContainerKeyDown}
        onKeyUp={onContainerKeyUp}
        className="overflow-hidden rounded-lg border border-neutral-200 bg-neutral-50 outline-none focus:ring-2 focus:ring-neutral-400 dark:border-neutral-800 dark:bg-neutral-950"
        style={{ height: STAGE_HEIGHT }}
      >
        {image ? (
          <Stage
            width={stageWidth}
            height={STAGE_HEIGHT}
            ref={stageRef}
            draggable={(!editingId && currentPolygon.length === 0) || panMode}
            onWheel={handleWheel}
            onClick={handleStageClick}
            onTap={handleStageClick}
            onMouseDown={() => containerRef.current?.focus()}
            style={{ cursor: panMode ? "grab" : "crosshair" }}
          >
            <Layer>
              <KonvaImage image={image} width={imageWidth} height={imageHeight} listening />
            </Layer>
            <Layer>
              {annotations.map((a) => {
                // Skip legacy / malformed rows defensively; the DB now
                // enforces the shape via CHECK but we still harden the
                // renderer against bad data.
                if (!isWellFormedPolygon(a.polygon)) return null;
                // The annotation under edit is rendered separately (with
                // handles) below — skip it here to avoid double draw.
                if (a.id === editingId) return null;
                const cls = TISSUE_CLASS_BY_KEY[a.class];
                const color = cls?.color ?? "#888";
                const isOwn = a.owner_id === currentUserId;
                const dim = editingId !== null;
                return (
                  <Line
                    key={a.id}
                    points={flattenPoints(a.polygon)}
                    closed
                    fill={hexToRgba(color, dim ? 0.08 : 0.25)}
                    stroke={color}
                    strokeWidth={2}
                    strokeScaleEnabled={false}
                    opacity={dim ? 0.4 : 1}
                    listening={!dim}
                    onClick={() => {
                      if (isOwn) startEditing(a);
                    }}
                    onTap={() => {
                      if (isOwn) startEditing(a);
                    }}
                  />
                );
              })}
              {/* Polygon currently being EDITED: outline + per-vertex
                  drag handles + midpoint "+" markers for inserts. */}
              {editingId && editingPolygon.length > 0 && (
                <>
                  <Line
                    points={flattenPoints(editingPolygon)}
                    closed
                    fill={hexToRgba(editingClassColor, 0.2)}
                    stroke={editingClassColor}
                    strokeWidth={2}
                    strokeScaleEnabled={false}
                    dash={[8, 4]}
                    dashEnabled
                    listening={false}
                  />
                  {/* Midpoint markers (insert a vertex on click) */}
                  {editingPolygon.map((p, i) => {
                    const next = editingPolygon[(i + 1) % editingPolygon.length];
                    const mx = (p[0] + next[0]) / 2;
                    const my = (p[1] + next[1]) / 2;
                    return (
                      <Circle
                        key={`mid-${i}-${mx.toFixed(1)}-${my.toFixed(1)}`}
                        x={mx}
                        y={my}
                        radius={VERTEX_RADIUS_PX_VIEW * 0.7}
                        fill="#ffffff"
                        stroke={editingClassColor}
                        strokeWidth={1}
                        strokeScaleEnabled={false}
                        opacity={0.7}
                        onClick={() => insertEditingVertex(i, mx, my)}
                        onTap={() => insertEditingVertex(i, mx, my)}
                      />
                    );
                  })}
                  {/* Vertex handles: drag to move, Shift-click to delete. */}
                  {editingPolygon.map((p, i) => (
                    <Circle
                      key={`h${i}`}
                      x={p[0]}
                      y={p[1]}
                      radius={VERTEX_RADIUS_PX_VIEW * 1.2}
                      fill="#ffffff"
                      stroke={editingClassColor}
                      strokeWidth={2}
                      strokeScaleEnabled={false}
                      draggable
                      onDragMove={(e) => moveEditingVertex(i, e.target.x(), e.target.y())}
                      // Shift+click deletes a vertex.  We deliberately do
                      // NOT bind onTap to the same action — touch users
                      // would lose vertices on every accidental tap that
                      // didn't translate into a drag.  Touch-only delete
                      // is exposed via the toolbar instead.
                      onClick={(e) => {
                        if (e.evt.shiftKey) deleteEditingVertex(i);
                      }}
                    />
                  ))}
                </>
              )}
              {currentPolygon.length > 0 && (
                <>
                  <Line
                    points={flattenPoints(currentPolygon)}
                    closed={false}
                    stroke={currentClassColor}
                    strokeWidth={2}
                    strokeScaleEnabled={false}
                    dash={[6, 4]}
                    dashEnabled
                  />
                  {currentPolygon.map((p, i) => (
                    <Circle
                      key={`v${i}-${p[0]}-${p[1]}`}
                      x={p[0]}
                      y={p[1]}
                      radius={VERTEX_RADIUS_PX_VIEW}
                      fill={currentClassColor}
                      strokeScaleEnabled={false}
                    />
                  ))}
                </>
              )}
            </Layer>
          </Stage>
        ) : (
          <p className="p-8 text-center text-sm text-neutral-500">画像を読み込み中…</p>
        )}
      </div>

      {editingId ? (
        <div className="flex flex-wrap items-center gap-2 rounded border border-amber-300 bg-amber-50 p-2 text-sm dark:border-amber-700 dark:bg-amber-950/40">
          <span className="text-xs font-medium text-amber-800 dark:text-amber-200">編集中</span>
          <label className="flex items-center gap-1 text-xs">
            クラス:
            <select
              value={editingClass}
              onChange={(e) => setEditingClass(e.target.value as TissueClassKey)}
              className="rounded border border-neutral-300 bg-transparent px-1 py-0.5 text-xs dark:border-neutral-700"
            >
              {TISSUE_CLASSES.map((c) => (
                <option key={c.key} value={c.key}>
                  {c.label}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            onClick={() => void saveEdits()}
            disabled={saving || editingPolygon.length < 3}
            className="rounded bg-neutral-900 px-3 py-1.5 font-medium text-white disabled:opacity-50 dark:bg-white dark:text-neutral-900"
          >
            {saving ? "保存中…" : "変更を保存 (Enter)"}
          </button>
          <button
            type="button"
            onClick={cancelEditing}
            className="rounded border border-neutral-300 px-3 py-1.5 dark:border-neutral-700"
          >
            取消 (Esc)
          </button>
          <button
            type="button"
            onClick={() => void deleteEditingAnnotation()}
            className="rounded border border-red-300 px-3 py-1.5 text-red-700 hover:bg-red-50 dark:border-red-800 dark:text-red-300 dark:hover:bg-red-950/40"
          >
            削除
          </button>
          <span className="text-xs text-neutral-600 dark:text-neutral-400">
            ハンドルをドラッグで移動 / <kbd className="rounded border px-1">Shift</kbd>
            +クリックで点削除 / 中点をクリックで点追加 ({editingPolygon.length} 点)
          </span>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <button
            type="button"
            onClick={savePolygon}
            disabled={saving || currentPolygon.length < 3}
            className="rounded bg-neutral-900 px-3 py-1.5 font-medium text-white disabled:opacity-50 dark:bg-white dark:text-neutral-900"
          >
            {saving ? "保存中…" : "確定して保存 (Enter)"}
          </button>
          <button
            type="button"
            onClick={undoLast}
            disabled={currentPolygon.length === 0}
            className="rounded border border-neutral-300 px-3 py-1.5 disabled:opacity-50 dark:border-neutral-700"
          >
            1点戻す (BS)
          </button>
          <button
            type="button"
            onClick={cancelCurrent}
            disabled={currentPolygon.length === 0}
            className="rounded border border-neutral-300 px-3 py-1.5 disabled:opacity-50 dark:border-neutral-700"
          >
            取消 (Esc)
          </button>
          <span className="text-xs text-neutral-500">
            クリックで頂点追加、<kbd className="rounded border px-1">Space</kbd>
            押しながらドラッグでパン、ホイールでズーム、既存ポリゴン（自分のもの）をクリックで編集
          </span>
        </div>
      )}

      {error && (
        <p className="rounded bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950 dark:text-red-200">
          {error}
        </p>
      )}

      <div className="text-sm">
        <h3 className="font-medium">保存済みアノテーション（{annotations.length} 件）</h3>
        <ul className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
          {annotations.map((a) => {
            const cls = TISSUE_CLASS_BY_KEY[a.class];
            const ptCount = isWellFormedPolygon(a.polygon) ? a.polygon.length : "?";
            const isOwn = a.owner_id === currentUserId;
            const isEditing = a.id === editingId;
            return (
              <li key={a.id}>
                <button
                  type="button"
                  onClick={() => (isOwn ? startEditing(a) : undefined)}
                  disabled={!isOwn}
                  className={`flex w-full items-center gap-2 rounded border px-2 py-1 text-left text-xs transition-colors ${
                    isEditing
                      ? "border-amber-500 bg-amber-50 dark:border-amber-600 dark:bg-amber-950/40"
                      : "border-neutral-200 dark:border-neutral-800"
                  } ${isOwn ? "hover:bg-neutral-50 dark:hover:bg-neutral-900" : "cursor-not-allowed opacity-60"}`}
                  title={isOwn ? "クリックで編集" : "他ユーザーのアノテーション"}
                >
                  <span
                    aria-hidden
                    className="inline-block h-3 w-3 shrink-0 rounded-sm"
                    style={{ backgroundColor: cls?.color ?? "#888" }}
                  />
                  <span className="truncate">{cls?.label ?? a.class}</span>
                  <span className="ml-auto text-neutral-500">{ptCount} pts</span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
