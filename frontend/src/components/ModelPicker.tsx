/**
 * ModelPicker
 * -----------
 * Compact pill-shaped dropdown that lets the user pick which Claude model
 * handles the **next** turn. The choice is applied per-send (read via a ref
 * in ``useChat``), so switching mid-conversation affects future replies only
 * — already-streamed messages are not rewritten.
 *
 * The available model list and the server default come from
 * ``/api/config`` (``OrchestratorConfig.available_models``). The chosen ID
 * is persisted in ``localStorage`` under :data:`STORAGE_KEY` so it survives
 * page reloads; if the stored ID is no longer in the allowlist we fall back
 * to the server default so the UI never posts a forbidden model.
 */

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import type { OrchestratorModelOption } from "../api";

const STORAGE_KEY = "bb_orchestrator_model";

/** Read the stored model ID, validating against the current allowlist. */
export function readStoredModel(
  models: OrchestratorModelOption[] | undefined,
  defaultModel: string | undefined,
): string | null {
  if (!models || models.length === 0) return defaultModel ?? null;
  const ids = new Set(models.map((m) => m.id));
  try {
    const raw =
      typeof localStorage !== "undefined"
        ? localStorage.getItem(STORAGE_KEY)
        : null;
    if (raw && ids.has(raw)) return raw;
  } catch {
    /* localStorage unavailable — fall through to default */
  }
  if (defaultModel && ids.has(defaultModel)) return defaultModel;
  return models[0]?.id ?? null;
}

/** Persist a picked model; no-op if storage is unavailable. */
export function writeStoredModel(modelId: string): void {
  try {
    if (typeof localStorage !== "undefined") {
      localStorage.setItem(STORAGE_KEY, modelId);
    }
  } catch {
    /* ignore quota / private mode */
  }
}

const TIER_BADGE_CLASS: Record<string, string> = {
  fast: "bg-emerald-100 text-emerald-700",
  balanced: "bg-amber-100 text-amber-700",
  max: "bg-indigo-100 text-indigo-700",
  custom: "bg-slate-100 text-slate-600 dark:bg-brand-100 dark:text-brand-muted",
};

function tierLabel(tier: string): string {
  switch (tier) {
    case "fast":
      return "Fast";
    case "balanced":
      return "Balanced";
    case "max":
      return "Max";
    default:
      return tier || "Custom";
  }
}

interface ModelPickerProps {
  /** Full allowlist from ``/api/config``; empty/undefined hides the picker. */
  models: OrchestratorModelOption[] | undefined;
  /** Currently selected model ID (controlled). */
  value: string | null;
  /** Called when the user picks a new model. Persistence is handled by the parent. */
  onChange: (modelId: string) => void;
  /** Disable interaction while a turn is streaming so switches don't fire mid-call. */
  disabled?: boolean;
  /**
   * Open the panel above (default) or below the trigger, matching the
   * conventions used by :class:`TokenBudgetPopover`.
   */
  panelPlacement?: "above" | "below";
  /** Extra classes merged onto the trigger root (e.g. flex alignment). */
  className?: string;
}

export default function ModelPicker({
  models,
  value,
  onChange,
  disabled = false,
  panelPlacement = "above",
  className,
}: ModelPickerProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const panelId = useId();

  const selected = useMemo(() => {
    if (!models) return undefined;
    return models.find((m) => m.id === value) ?? models.find((m) => m.is_default);
  }, [models, value]);

  const close = useCallback(() => setOpen(false), []);

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        close();
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") close();
    }
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);

  // Hide the picker entirely when the server hasn't advertised any models
  // (older backend, /api/config still loading, env-disabled allowlist).
  if (!models || models.length === 0) return null;
  // Single-model allowlist: picker has nothing to offer, so don't draw it.
  if (models.length === 1) return null;

  /*
   * Always left-align the panel to the trigger; the width clamp keeps it
   * from overflowing the right edge on narrow viewports. Centering via
   * ``-translate-x-1/2`` would push the panel off-screen to the left
   * when the picker sits near the composer's left edge (mobile footer).
   */
  const panelPositionClass =
    panelPlacement === "below"
      ? "left-0 top-full z-40 mt-2 w-[min(18rem,calc(100vw-1rem))] max-w-[calc(100vw-env(safe-area-inset-left)-env(safe-area-inset-right)-0.5rem)] sm:w-72"
      : "bottom-full left-0 z-40 mb-2 w-[min(18rem,calc(100vw-1rem))] max-w-[calc(100vw-env(safe-area-inset-left)-env(safe-area-inset-right)-0.5rem)] sm:w-72";

  const triggerLabel = selected?.label ?? "Model";

  return (
    <div
      ref={rootRef}
      className={["relative min-w-0 self-center", className].filter(Boolean).join(" ")}
    >
      <button
        type="button"
        onClick={() => !disabled && setOpen((v) => !v)}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={panelId}
        title={selected ? `${selected.label} — ${selected.description}` : "Pick a model"}
        className={[
          "flex h-12 min-h-[48px] min-w-0 max-w-full items-center gap-1.5 rounded-full border px-3 text-sm font-medium text-brand-800 transition-colors dark:text-brand-muted dark:hover:text-brand-900",
          "sm:min-h-[44px] sm:h-11",
          open
            ? "border-brand-500 bg-brand-50/90 shadow-inner ring-2 ring-brand-400/30 dark:bg-brand-100/80 dark:ring-brand-500/25"
            : "border-brand-border bg-white hover:border-brand-400 hover:bg-brand-50/80 dark:bg-brand-100 dark:hover:bg-white/10",
          disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer",
        ].join(" ")}
      >
        <span className="min-w-0 truncate max-w-[5.5rem] sm:max-w-[7.5rem]">{triggerLabel}</span>
        <svg
          aria-hidden
          width="12"
          height="12"
          viewBox="0 0 12 12"
          className={`shrink-0 transition-transform ${open ? "rotate-180" : ""}`}
        >
          <path d="M2 4.5l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {open && (
        <div
          id={panelId}
          role="listbox"
          aria-label="Claude model"
          className={[
            "absolute rounded-2xl border border-brand-border bg-white p-1.5 shadow-[0_18px_56px_-18px_rgba(15,23,42,0.28)] dark:shadow-[0_18px_56px_-18px_rgba(0,0,0,0.55)]",
            panelPositionClass,
          ].join(" ")}
        >
          <div className="px-2 pb-1.5 pt-1 text-[0.7rem] font-medium uppercase tracking-wide text-brand-muted">
            Model for next turn
          </div>
          {models.map((m) => {
            const isSelected = m.id === (value ?? selected?.id);
            const badgeClass =
              TIER_BADGE_CLASS[m.tier] ?? TIER_BADGE_CLASS.custom;
            return (
              <button
                key={m.id}
                type="button"
                role="option"
                aria-selected={isSelected}
                onClick={() => {
                  onChange(m.id);
                  close();
                }}
                className={[
                  "flex w-full flex-col gap-0.5 rounded-xl px-2.5 py-2 text-left transition-colors",
                  isSelected
                    ? "bg-brand-50 ring-1 ring-brand-300"
                    : "hover:bg-brand-50/60",
                ].join(" ")}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-sm font-medium text-brand-900">
                    {m.label}
                    {m.is_default && (
                      <span className="ml-1.5 text-[0.65rem] font-normal text-brand-muted">
                        (default)
                      </span>
                    )}
                  </span>
                  <span
                    className={[
                      "shrink-0 rounded-full px-1.5 py-0.5 text-[0.65rem] font-semibold uppercase tracking-wide",
                      badgeClass,
                    ].join(" ")}
                  >
                    {tierLabel(m.tier)}
                  </span>
                </div>
                {m.description && (
                  <div className="text-[0.75rem] leading-snug text-brand-muted">
                    {m.description}
                  </div>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
