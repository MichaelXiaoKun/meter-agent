import { useCallback, useEffect, useRef, useState } from "react";

interface ImageViewerProps {
  src: string;
  alt?: string;
  onClose: () => void;
}

export default function ImageViewer({ src, alt, onClose }: ImageViewerProps) {
  const [scale, setScale] = useState(1);
  const [translate, setTranslate] = useState({ x: 0, y: 0 });
  const dragging = useRef(false);
  const lastPos = useRef({ x: 0, y: 0 });

  const resetView = useCallback(() => {
    setScale(1);
    setTranslate({ x: 0, y: 0 });
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
      if (e.key === "+" || e.key === "=") setScale((s) => Math.min(s + 0.25, 5));
      if (e.key === "-") setScale((s) => Math.max(s - 0.25, 0.25));
      if (e.key === "0") resetView();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, resetView]);

  function handleWheel(e: React.WheelEvent) {
    e.stopPropagation();
    const delta = e.deltaY > 0 ? -0.15 : 0.15;
    setScale((s) => Math.min(Math.max(s + delta, 0.25), 5));
  }

  function handlePointerDown(e: React.PointerEvent) {
    if (e.button !== 0) return;
    dragging.current = true;
    lastPos.current = { x: e.clientX, y: e.clientY };
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
  }

  function handlePointerMove(e: React.PointerEvent) {
    if (!dragging.current) return;
    const dx = e.clientX - lastPos.current.x;
    const dy = e.clientY - lastPos.current.y;
    lastPos.current = { x: e.clientX, y: e.clientY };
    setTranslate((t) => ({ x: t.x + dx, y: t.y + dy }));
  }

  function handlePointerUp() {
    dragging.current = false;
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
      onClick={onClose}
    >
      {/* Toolbar */}
      <div
        className="absolute top-4 left-1/2 z-10 flex -translate-x-1/2 items-center gap-1 rounded-xl bg-white/95 px-2 py-1.5 shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          onClick={() => setScale((s) => Math.max(s - 0.25, 0.25))}
          className="rounded-lg px-2.5 py-1 text-sm font-medium text-gray-700 hover:bg-gray-100"
          title="Zoom out (-)"
        >
          &minus;
        </button>
        <span className="min-w-[3.5rem] text-center text-xs text-gray-500">
          {Math.round(scale * 100)}%
        </span>
        <button
          onClick={() => setScale((s) => Math.min(s + 0.25, 5))}
          className="rounded-lg px-2.5 py-1 text-sm font-medium text-gray-700 hover:bg-gray-100"
          title="Zoom in (+)"
        >
          +
        </button>
        <div className="mx-1 h-4 w-px bg-gray-200" />
        <button
          onClick={resetView}
          className="rounded-lg px-2.5 py-1 text-xs font-medium text-gray-700 hover:bg-gray-100"
          title="Reset (0)"
        >
          Reset
        </button>
        <div className="mx-1 h-4 w-px bg-gray-200" />
        <button
          onClick={onClose}
          className="rounded-lg px-2.5 py-1 text-sm font-medium text-gray-700 hover:bg-gray-100"
          title="Close (Esc)"
        >
          &times;
        </button>
      </div>

      {/* Image */}
      <div
        className="cursor-grab select-none active:cursor-grabbing"
        onClick={(e) => e.stopPropagation()}
        onWheel={handleWheel}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
      >
        <img
          src={src}
          alt={alt ?? "Plot"}
          draggable={false}
          className="max-h-[85vh] max-w-[90vw] rounded-lg shadow-2xl"
          style={{
            transform: `translate(${translate.x}px, ${translate.y}px) scale(${scale})`,
            transformOrigin: "center center",
            transition: dragging.current ? "none" : "transform 0.15s ease-out",
          }}
        />
      </div>
    </div>
  );
}
