import { useState } from "react";

export type ToastType = "success" | "error" | "warning" | "info";

export interface Toast {
  id: string;
  type: ToastType;
  title: string;
  message?: string;
  action?: { label: string; onClick: () => void };
  duration?: number; // ms, 0 = persistent
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
