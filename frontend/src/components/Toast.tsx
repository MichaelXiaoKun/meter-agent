import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";

export type ToastType = "success" | "error" | "warning" | "info";

export interface Toast {
  id: string;
  type: ToastType;
  title: string;
  message?: string;
  action?: { label: string; onClick: () => void };
  duration?: number; // ms, 0 = persistent
}

const icons: Record<ToastType, string> = {
  success: "✓",
  error: "⚠",
  warning: "!",
  info: "ℹ",
};

const colors: Record<ToastType, string> = {
  success: "bg-emerald-50 border-emerald-200 dark:bg-emerald-950/40 dark:border-emerald-800/50",
  error: "bg-red-50 border-red-200 dark:bg-red-950/40 dark:border-red-800/50",
  warning: "bg-amber-50 border-amber-200 dark:bg-amber-950/40 dark:border-amber-800/50",
  info: "bg-blue-50 border-blue-200 dark:bg-blue-950/40 dark:border-blue-800/50",
};

const textColors: Record<ToastType, string> = {
  success: "text-emerald-900 dark:text-emerald-100",
  error: "text-red-900 dark:text-red-100",
  warning: "text-amber-900 dark:text-amber-100",
  info: "text-blue-900 dark:text-blue-100",
};

const iconColors: Record<ToastType, string> = {
  success: "text-emerald-600 dark:text-emerald-400",
  error: "text-red-600 dark:text-red-400",
  warning: "text-amber-600 dark:text-amber-400",
  info: "text-blue-600 dark:text-blue-400",
};

function ToastItem({
  toast,
  onClose,
}: {
  toast: Toast;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!toast.duration || toast.duration === 0) return;
    const timer = setTimeout(onClose, toast.duration);
    return () => clearTimeout(timer);
  }, [toast.duration, onClose]);

  return (
    <motion.div
      layout
      initial={{ opacity: 0, x: 400 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 400 }}
      transition={{ type: "spring", stiffness: 300, damping: 30 }}
      className={`rounded-lg border px-4 py-3 ${colors[toast.type]} ${textColors[toast.type]}`}
      role="status"
    >
      <div className="flex items-start gap-3">
        <div className={`shrink-0 text-lg font-bold ${iconColors[toast.type]}`}>
          {icons[toast.type]}
        </div>
        <div className="flex-1 min-w-0">
          <p className="font-medium text-sm">{toast.title}</p>
          {toast.message && (
            <p className="text-xs mt-0.5 opacity-90">{toast.message}</p>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {toast.action && (
            <button
              onClick={() => {
                toast.action!.onClick();
                onClose();
              }}
              className="text-xs font-medium px-2 py-1 rounded hover:opacity-80 transition-opacity"
            >
              {toast.action.label}
            </button>
          )}
          <button
            onClick={onClose}
            className="text-lg leading-none opacity-60 hover:opacity-100 transition-opacity"
            aria-label="Close"
          >
            ×
          </button>
        </div>
      </div>
    </motion.div>
  );
}

export function ToastContainer({
  toasts,
  onClose,
}: {
  toasts: Toast[];
  onClose: (id: string) => void;
}) {
  return (
    <div
      className="fixed bottom-4 right-4 z-50 space-y-2 pointer-events-none"
      aria-live="polite"
      aria-atomic="false"
    >
      <AnimatePresence mode="popLayout">
        {toasts.map((toast) => (
          <div key={toast.id} className="pointer-events-auto">
            <ToastItem
              toast={toast}
              onClose={() => onClose(toast.id)}
            />
          </div>
        ))}
      </AnimatePresence>
    </div>
  );
}

export function useToast() {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const show = (toast: Omit<Toast, "id">) => {
    const id = `toast-${Date.now()}-${Math.random()}`;
    const newToast: Toast = { ...toast, id, duration: toast.duration ?? 4000 };
    setToasts((prev) => [...prev, newToast]);
    return id;
  };

  const dismiss = (id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  };

  return {
    toasts,
    show,
    dismiss,
    success: (title: string, message?: string) =>
      show({ type: "success", title, message }),
    error: (title: string, message?: string) =>
      show({ type: "error", title, message }),
    warning: (title: string, message?: string) =>
      show({ type: "warning", title, message }),
    info: (title: string, message?: string) =>
      show({ type: "info", title, message }),
  };
}
