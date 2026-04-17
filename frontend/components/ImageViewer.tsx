"use client";

import { TransformComponent, TransformWrapper } from "react-zoom-pan-pinch";

export function ImageViewer({ src, alt }: { src: string; alt: string }) {
  return (
    <div className="overflow-hidden rounded-lg border border-neutral-200 bg-neutral-50 dark:border-neutral-800 dark:bg-neutral-950">
      <TransformWrapper
        minScale={0.1}
        maxScale={20}
        initialScale={1}
        centerOnInit
        wheel={{ step: 0.2 }}
        doubleClick={{ disabled: false, mode: "reset" }}
      >
        {({ zoomIn, zoomOut, resetTransform }) => (
          <div className="relative">
            <div className="absolute right-3 top-3 z-10 flex gap-1 rounded bg-white/80 p-1 shadow dark:bg-neutral-900/80">
              <button
                type="button"
                onClick={() => zoomOut()}
                className="h-8 w-8 rounded text-sm hover:bg-neutral-100 dark:hover:bg-neutral-800"
                aria-label="zoom out"
              >
                −
              </button>
              <button
                type="button"
                onClick={() => resetTransform()}
                className="h-8 rounded px-2 text-xs hover:bg-neutral-100 dark:hover:bg-neutral-800"
                aria-label="reset zoom"
              >
                reset
              </button>
              <button
                type="button"
                onClick={() => zoomIn()}
                className="h-8 w-8 rounded text-sm hover:bg-neutral-100 dark:hover:bg-neutral-800"
                aria-label="zoom in"
              >
                +
              </button>
            </div>
            <TransformComponent
              wrapperClass="!w-full !h-[70vh]"
              contentClass="!w-full !h-full flex items-center justify-center"
            >
              <img
                src={src}
                alt={alt}
                className="max-h-full max-w-full select-none"
                draggable={false}
              />
            </TransformComponent>
          </div>
        )}
      </TransformWrapper>
    </div>
  );
}
