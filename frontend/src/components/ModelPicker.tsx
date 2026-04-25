/**
 * ModelPicker
 * -----------
 * Compact pill-shaped dropdown that lets the user pick which model handles
 * the **next** turn. Models are grouped by provider (Anthropic, OpenAI, Gemini).
 *
 * The available model list and the server default come from
 * ``/api/config`` (``OrchestratorConfig.available_models``). The chosen ID
 * is persisted in ``localStorage`` so it survives page reloads; if the stored
 * ID is no longer in the allowlist we fall back to the server default.
 */

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import type { OrchestratorModelOption } from "../api";

// ── Tier badges ──────────────────────────────────────────────────────────────

const TIER_BADGE_CLASS: Record<string, string> = {
  fast: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950/55 dark:text-emerald-200",
  balanced: "bg-amber-100 text-amber-700 dark:bg-amber-950/55 dark:text-amber-200",
  max: "bg-indigo-100 text-indigo-700 dark:bg-indigo-950/50 dark:text-indigo-200",
  reasoning: "bg-purple-100 text-purple-700 dark:bg-purple-950/50 dark:text-purple-200",
  custom: "bg-slate-100 text-slate-600 dark:bg-brand-100 dark:text-brand-muted",
};

function tierLabel(tier: string): string {
  const labels: Record<string, string> = {
    fast: "Fast",
    balanced: "Balanced",
    max: "Max",
    reasoning: "Reasoning",
  };
  return labels[tier] ?? (tier || "Custom");
}

// ── Provider metadata ─────────────────────────────────────────────────────────

interface ProviderMeta {
  label: string;
  /** Inline SVG shown next to the provider group header and in the trigger pill. */
  icon: React.ReactNode;
}

function AnthropicIcon({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      className={className ?? "h-3.5 w-3.5 shrink-0"}
      fill="currentColor"
    >
      {/* Stylised "A" shape — Anthropic's lettermark */}
      <path d="M13.827 3.52h-3.654L5 20.48h3.213l.913-2.805h5.755l.914 2.805H19l-5.173-16.96zm-3.847 11.83 1.977-6.079 1.977 6.079H9.98z" />
    </svg>
  );
}

function OpenAIIcon({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      className={className ?? "h-3.5 w-3.5 shrink-0"}
      fill="currentColor"
    >
      <path d="M22.282 9.821a5.985 5.985 0 0 0-.516-4.91 6.046 6.046 0 0 0-6.51-2.9A6.065 6.065 0 0 0 4.981 4.18a5.985 5.985 0 0 0-3.998 2.9 6.046 6.046 0 0 0 .743 7.097 5.98 5.98 0 0 0 .51 4.911 6.051 6.051 0 0 0 6.515 2.9A5.985 5.985 0 0 0 13.26 24a6.056 6.056 0 0 0 5.772-4.206 5.99 5.99 0 0 0 3.997-2.9 6.056 6.056 0 0 0-.747-7.073zM13.26 22.43a4.476 4.476 0 0 1-2.876-1.04l.141-.081 4.779-2.758a.795.795 0 0 0 .392-.681v-6.737l2.02 1.168a.071.071 0 0 1 .038.052v5.583a4.504 4.504 0 0 1-4.494 4.494zM3.6 18.304a4.47 4.47 0 0 1-.535-3.014l.142.085 4.783 2.759a.771.771 0 0 0 .78 0l5.843-3.369v2.332a.08.08 0 0 1-.033.062L9.74 19.95a4.5 4.5 0 0 1-6.14-1.646zM2.34 7.896a4.485 4.485 0 0 1 2.366-1.973V11.6a.766.766 0 0 0 .388.676l5.815 3.355-2.02 1.168a.076.076 0 0 1-.071 0l-4.83-2.786A4.504 4.504 0 0 1 2.34 7.896zm16.597 3.855l-5.833-3.387L15.119 7.2a.076.076 0 0 1 .071 0l4.83 2.791a4.494 4.494 0 0 1-.676 8.105v-5.678a.79.79 0 0 0-.407-.667zm2.01-3.023l-.141-.085-4.774-2.782a.776.776 0 0 0-.785 0L9.409 9.23V6.897a.066.066 0 0 1 .028-.061l4.83-2.787a4.5 4.5 0 0 1 6.68 4.66zm-12.64 4.135l-2.02-1.164a.08.08 0 0 1-.038-.057V6.075a4.5 4.5 0 0 1 7.375-3.453l-.142.08L8.704 5.46a.795.795 0 0 0-.393.681zm1.097-2.365l2.602-1.5 2.607 1.5v2.999l-2.597 1.5-2.607-1.5z" />
    </svg>
  );
}

function GeminiIcon({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      className={className ?? "h-3.5 w-3.5 shrink-0"}
      fill="currentColor"
    >
      {/* Google "G" lettermark */}
      <path d="M12.48 10.92v3.28h7.84c-.24 1.84-.853 3.187-1.787 4.133-1.147 1.147-2.933 2.4-6.053 2.4-4.827 0-8.6-3.893-8.6-8.72s3.773-8.72 8.6-8.72c2.6 0 4.507 1.027 5.907 2.347l2.307-2.307C18.747 1.44 16.133 0 12.48 0 5.867 0 .307 5.387.307 12s5.56 12 12.173 12c3.573 0 6.267-1.173 8.373-3.36 2.16-2.16 2.84-5.213 2.84-7.667 0-.76-.053-1.467-.173-2.053H12.48z" />
    </svg>
  );
}

const PROVIDER_META: Record<string, ProviderMeta> = {
  anthropic: {
    label: "Anthropic",
    icon: <AnthropicIcon />,
  },
  openai: {
    label: "OpenAI",
    icon: <OpenAIIcon />,
  },
  gemini: {
    label: "Google Gemini",
    icon: <GeminiIcon />,
  },
};

const PROVIDER_ORDER = ["anthropic", "openai", "gemini"];

function providerLabel(provider: string): string {
  return PROVIDER_META[provider]?.label ?? provider;
}

function ProviderIcon({
  provider,
  className,
}: {
  provider: string;
  className?: string;
}) {
  const meta = PROVIDER_META[provider];
  if (!meta) {
    return (
      <span className={["shrink-0 text-[0.65rem] font-bold uppercase", className].filter(Boolean).join(" ")}>
        {provider[0]?.toUpperCase() ?? "?"}
      </span>
    );
  }
  return (
    <span className={["shrink-0 text-brand-600 dark:text-brand-muted", className].filter(Boolean).join(" ")}>
      {meta.icon}
    </span>
  );
}

// ── Component ─────────────────────────────────────────────────────────────────

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
   * Open the panel above (default) or below the trigger.
   */
  panelPlacement?: "above" | "below";
  /** Extra classes merged onto the trigger root. */
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

  // Group models by provider in a stable display order
  const groups = useMemo(() => {
    if (!models) return [];
    const map = new Map<string, OrchestratorModelOption[]>();
    for (const m of models) {
      const p = m.provider ?? "custom";
      if (!map.has(p)) map.set(p, []);
      map.get(p)!.push(m);
    }
    const ordered: Array<{ provider: string; models: OrchestratorModelOption[] }> = [];
    for (const p of PROVIDER_ORDER) {
      if (map.has(p)) ordered.push({ provider: p, models: map.get(p)! });
    }
    for (const [p, ms] of map) {
      if (!PROVIDER_ORDER.includes(p)) ordered.push({ provider: p, models: ms });
    }
    return ordered;
  }, [models]);

  const close = useCallback(() => setOpen(false), []);

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) close();
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

  if (!models || models.length === 0) return null;
  if (models.length === 1) return null;

  const panelPositionClass =
    panelPlacement === "below"
      ? "left-0 top-full z-40 mt-2 w-[min(20rem,calc(100vw-1rem))] max-w-[calc(100vw-env(safe-area-inset-left)-env(safe-area-inset-right)-0.5rem)] sm:w-80"
      : "bottom-full left-0 z-40 mb-2 w-[min(20rem,calc(100vw-1rem))] max-w-[calc(100vw-env(safe-area-inset-left)-env(safe-area-inset-right)-0.5rem)] sm:w-80";

  const triggerLabel = selected?.label ?? "Model";

  return (
    <div
      ref={rootRef}
      className={["relative min-w-0 self-center", className].filter(Boolean).join(" ")}
    >
      {/* ── Trigger pill ── */}
      <button
        type="button"
        onClick={() => !disabled && setOpen((v) => !v)}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={panelId}
        title={selected ? `${selected.label} (${providerLabel(selected.provider)}) — ${selected.description}` : "Pick a model"}
        className={[
          "flex h-12 min-h-[48px] min-w-0 max-w-full items-center gap-1.5 rounded-full border px-3 text-sm font-medium text-brand-800 transition-colors dark:text-brand-muted dark:hover:text-brand-900",
          "sm:min-h-[44px] sm:h-11",
          open
            ? "border-brand-500 bg-brand-50/90 shadow-inner ring-2 ring-brand-400/30 dark:border-brand-border dark:bg-brand-100 dark:shadow-inner dark:ring-1 dark:ring-white/12"
            : "border-brand-border bg-white hover:border-brand-400 hover:bg-brand-50/80 dark:bg-brand-100 dark:hover:bg-white/10",
          disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer",
        ].join(" ")}
      >
        {selected?.provider && (
          <ProviderIcon provider={selected.provider} className="h-3.5 w-3.5 shrink-0" />
        )}
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

      {/* ── Dropdown panel ── */}
      {open && (
        <div
          id={panelId}
          role="listbox"
          aria-label="AI model"
          className={[
            "absolute rounded-2xl border border-brand-border bg-white shadow-[0_18px_56px_-18px_rgba(15,23,42,0.28)] dark:border-brand-border dark:bg-brand-100 dark:shadow-[0_18px_56px_-18px_rgba(0,0,0,0.55)]",
            panelPositionClass,
          ].join(" ")}
        >
          <div className="max-h-[min(70vh,24rem)] overflow-y-auto p-1.5">
            {groups.map(({ provider, models: groupModels }, gi) => (
            <div key={provider}>
              {/* Provider group header (only shown when there are multiple providers) */}
              {groups.length > 1 && (
                <div className={[
                  "flex items-center gap-1.5 px-2 pb-0.5 text-[0.68rem] font-semibold text-brand-muted",
                  gi > 0 ? "mt-2 pt-1.5 border-t border-brand-border/60" : "pt-0.5",
                ].join(" ")}>
                  <ProviderIcon provider={provider} className="h-3 w-3" />
                  <span>{providerLabel(provider)}</span>
                </div>
              )}

              {groupModels.map((m) => {
                const isSelected = m.id === (value ?? selected?.id);
                const badgeClass = TIER_BADGE_CLASS[m.tier] ?? TIER_BADGE_CLASS.custom;
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
                        ? "bg-brand-50 ring-1 ring-brand-300 dark:bg-white/[0.08] dark:ring-brand-border"
                        : "hover:bg-brand-50/60 dark:hover:bg-white/10",
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
          ))}
          </div>
        </div>
      )}
    </div>
  );
}
