"use client";

import { createClient } from "@/lib/supabase/client";
import type { AnnotationRow } from "@/lib/supabase/types";
import { TISSUE_CLASSES, TISSUE_CLASS_BY_KEY, type TissueClassKey } from "@/lib/tissue-classes";
import type Konva from "konva";
import type { KonvaEventObject } from "konva/lib/Node";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Circle, Image as KonvaImage, Layer, Line, Stage } from "react-konva";

export type AnnotationEditorProps = {
  imageId: string;
  imageUrl: string;
  imageWidth: number;
  imageHeight: number;
  initial: AnnotationRow[];
  currentUserId: string;
  canEdit: boolean;
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

export function AnnotationEditorInner({
  imageId,
  imageUrl,
  imageWidth,
  imageHeight,
  initial,
  currentUserId,
  canEdit,
}: AnnotationEditorProps) {
  const supabase = useMemo(() => createClient(), []);
  const stageRef = useRef<Konva.Stage | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);

  const [stageWidth, setStageWidth] = useState(960);
  const [image, setImage] = useState<HTMLImageElement | null>(null);

  const [annotations, setAnnotations] = useState<AnnotationRow[]>(initial);
  const [currentClass, setCurrentClass] = useState<TissueClassKey>("palisade");
  const [currentPolygon, setCurrentPolygon] = useState<number[][]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // Responsive stage width
  useEffect(() => {
    if (!containerRef.current) return;
    const el = containerRef.current;
    const ro = new ResizeObserver(() => setStageWidth(el.clientWidth));
    ro.observe(el);
    setStageWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  // Load image (HTMLImageElement for Konva.Image)
  useEffect(() => {
    const img = new window.Image();
    img.crossOrigin = "anonymous";
    img.src = imageUrl;
    img.onload = () => setImage(img);
    img.onerror = () => setError("画像の読み込みに失敗しました");
  }, [imageUrl]);

  // Initial fit: show whole image
  const initialScale = useMemo(() => {
    if (!image) return 1;
    return Math.min(stageWidth / imageWidth, STAGE_HEIGHT / imageHeight);
  }, [image, stageWidth, imageWidth, imageHeight]);

  // Apply initial scale once on load
  useEffect(() => {
    if (!stageRef.current || !image) return;
    const stage = stageRef.current;
    stage.scale({ x: initialScale, y: initialScale });
    // Centre
    const cx = (stageWidth - imageWidth * initialScale) / 2;
    const cy = (STAGE_HEIGHT - imageHeight * initialScale) / 2;
    stage.position({ x: cx, y: cy });
    stage.batchDraw();
  }, [image, initialScale, stageWidth, imageWidth, imageHeight]);

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
    if (!canEdit) return;
    // Ignore clicks on existing annotations (we use Line onClick for delete)
    if (e.target !== e.target.getStage() && e.target.className !== "Image") {
      return;
    }
    const stage = e.target.getStage();
    if (!stage) return;
    const pos = pointerInStageCoords(stage);
    if (!pos) return;
    setCurrentPolygon((prev) => [...prev, [pos.x, pos.y]]);
  };

  const cancelCurrent = useCallback(() => setCurrentPolygon([]), []);

  const undoLast = useCallback(() => {
    setCurrentPolygon((prev) => prev.slice(0, -1));
  }, []);

  const savePolygon = useCallback(async () => {
    if (!canEdit) return;
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
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }, [canEdit, currentPolygon, currentClass, imageId, currentUserId, supabase]);

  const deleteAnnotation = async (id: string) => {
    const { error: delErr } = await supabase.from("annotations").delete().eq("id", id);
    if (delErr) {
      setError(delErr.message);
      return;
    }
    setAnnotations((prev) => prev.filter((a) => a.id !== id));
  };

  // Keyboard: Enter closes polygon, Esc cancels, Backspace undoes last vertex
  useEffect(() => {
    if (!canEdit) return;
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (target?.tagName === "INPUT" || target?.tagName === "TEXTAREA") return;
      if (e.key === "Enter") {
        e.preventDefault();
        void savePolygon();
      } else if (e.key === "Escape") {
        cancelCurrent();
      } else if (e.key === "Backspace") {
        undoLast();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [canEdit, savePolygon, cancelCurrent, undoLast]);

  const currentClassColor = TISSUE_CLASS_BY_KEY[currentClass]?.color ?? "#888";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-neutral-500">クラス:</span>
        {TISSUE_CLASSES.map((c) => (
          <button
            key={c.key}
            type="button"
            onClick={() => setCurrentClass(c.key)}
            disabled={!canEdit}
            className={`flex items-center gap-1 rounded border px-2 py-1 ${
              currentClass === c.key
                ? "border-neutral-900 bg-neutral-100 dark:border-white dark:bg-neutral-800"
                : "border-neutral-300 dark:border-neutral-700"
            } disabled:opacity-50`}
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

      <div
        ref={containerRef}
        className="overflow-hidden rounded-lg border border-neutral-200 bg-neutral-50 dark:border-neutral-800 dark:bg-neutral-950"
        style={{ height: STAGE_HEIGHT }}
      >
        {image ? (
          <Stage
            width={stageWidth}
            height={STAGE_HEIGHT}
            ref={stageRef}
            draggable={canEdit ? currentPolygon.length === 0 : true}
            onWheel={handleWheel}
            onClick={handleStageClick}
            onTap={handleStageClick}
          >
            <Layer>
              <KonvaImage image={image} width={imageWidth} height={imageHeight} listening />
            </Layer>
            <Layer>
              {annotations.map((a) => {
                const cls = TISSUE_CLASS_BY_KEY[a.class];
                const color = cls?.color ?? "#888";
                const isOwn = a.owner_id === currentUserId;
                return (
                  <Line
                    key={a.id}
                    points={flattenPoints(a.polygon)}
                    closed
                    fill={hexToRgba(color, 0.25)}
                    stroke={color}
                    strokeWidth={2}
                    strokeScaleEnabled={false}
                    onClick={() => {
                      if (
                        canEdit &&
                        isOwn &&
                        window.confirm("このアノテーションを削除しますか？")
                      ) {
                        void deleteAnnotation(a.id);
                      }
                    }}
                    onTap={() => {
                      if (
                        canEdit &&
                        isOwn &&
                        window.confirm("このアノテーションを削除しますか？")
                      ) {
                        void deleteAnnotation(a.id);
                      }
                    }}
                  />
                );
              })}
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

      <div className="flex flex-wrap items-center gap-2 text-sm">
        {canEdit ? (
          <>
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
              クリックで頂点追加、ドラッグでパン、ホイールでズーム、既存ポリゴンをクリックで削除
            </span>
          </>
        ) : (
          <span className="text-xs text-neutral-500">
            他ユーザーのプライベート画像のため、アノテーションの編集はできません（閲覧のみ）。
          </span>
        )}
      </div>

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
            return (
              <li
                key={a.id}
                className="flex items-center gap-2 rounded border border-neutral-200 px-2 py-1 text-xs dark:border-neutral-800"
              >
                <span
                  aria-hidden
                  className="inline-block h-3 w-3 rounded-sm"
                  style={{ backgroundColor: cls?.color ?? "#888" }}
                />
                <span>{cls?.label ?? a.class}</span>
                <span className="ml-auto text-neutral-500">{a.polygon.length} pts</span>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
