import { useCallback, useEffect, useId, useRef, useState } from "react";

/** Fallbacks when /api/config is unavailable — match orchestrator defaults. */
export const DEFAULT_MODEL_CONTEXT_MAX = 200_000;
export const DEFAULT_INPUT_BUDGET_TARGET = 25_000;

/** @deprecated Use DEFAULT_MODEL_CONTEXT_MAX — kept for any external imports. */
export const MODEL_CONTEXT_TOKENS = DEFAULT_MODEL_CONTEXT_MAX;

/** Rolling TPM bar fallback if server config is missing (Haiku Tier-1 ITPM-style). */
export const DEFAULT_TPM_PER_MINUTE_GUIDE = 50_000;

const TPM_WARN = 0.7;
const TPM_CRITICAL = 0.9;

function formatCompactTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `${Math.round(n / 1000)}k`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return n.toLocaleString();
}

function BarChartIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="currentColor"
      className={className}
      aria-hidden
    >
      <rect x="3" y="13" width="5" height="8" rx="1" className="opacity-70" />
      <rect x="9.5" y="8" width="5" height="13" rx="1" />
      <rect x="16" y="11" width="5" height="10" rx="1" className="opacity-85" />
    </svg>
  );
}

function TokenBudgetPanel({
  tokenUsage,
  tpmPerMinuteGuide,
  tpmServerSliding60s,
  inputBudgetTarget,
  modelContextMax,
}: {
  tokenUsage: { tokens: number; pct: number };
  tpmPerMinuteGuide: number;
  tpmServerSliding60s: number;
  inputBudgetTarget: number;
  modelContextMax: number;
}) {
  const used = Math.max(0, tokenUsage.tokens);
  const budgetDen = Math.max(1, inputBudgetTarget);
  const budgetPct = Math.min(1, used / budgetDen);
  const ctxBarPct = Math.min(100, budgetPct * 100);
  const modelPctDisplay = budgetPct * 100;
  const remainingBeforeCompress = Math.max(0, budgetDen - used);
  const nearContextLimit = budgetPct >= 0.85;
  const warnContext = budgetPct >= 0.55;
  const barColor = nearContextLimit
    ? "bg-red-500"
    : warnContext
      ? "bg-amber-500"
      : "bg-brand-500";

  const tpm = Math.max(0, tpmServerSliding60s);
  const tpmBarPct = Math.min(100, (tpm / tpmPerMinuteGuide) * 100);
  const tpmWarn = tpm >= TPM_WARN * tpmPerMinuteGuide;
  const tpmCrit =
    tpm >= TPM_CRITICAL * tpmPerMinuteGuide || tpm > tpmPerMinuteGuide;
  const tpmBarColor = tpmCrit
    ? "bg-red-500"
    : tpmWarn
      ? "bg-amber-500"
      : "bg-brand-500";

  const hasThreadEstimate = used > 0 || budgetPct > 0;
  const hasAnySignal = hasThreadEstimate || tpm > 0;

  return (
    <div
      className={[
        "flex w-[min(100%,15.5rem)] flex-col justify-center rounded-xl border px-2.5 py-2 shadow-lg transition-colors",
        tpmCrit
          ? "border-red-300 bg-red-50/95"
          : tpmWarn
            ? "border-amber-300 bg-amber-50/90"
            : hasAnySignal
              ? "border-brand-border bg-white"
              : "border-brand-border/90 bg-brand-50/80",
      ].join(" ")}
    >
      <div className="flex items-center justify-between gap-1">
        <span className="text-[10px] font-semibold uppercase tracking-wide text-brand-muted">
          Model context
        </span>
        <span className="text-[10px] text-brand-muted tabular-nums">
          {hasThreadEstimate ? (
            <>
              ~{modelPctDisplay < 10 ? modelPctDisplay.toFixed(1) : modelPctDisplay.toFixed(0)}%
              <span className="text-brand-muted/80">
                {" "}
                · {formatCompactTokens(inputBudgetTarget)} target
              </span>
            </>
          ) : (
            "—"
          )}
        </span>
      </div>
      <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-brand-border">
        <div
          className={`h-full rounded-full transition-all ${barColor}`}
          style={{ width: `${ctxBarPct}%` }}
        />
      </div>
      <p className="mt-1 text-[11px] leading-tight text-brand-900 tabular-nums">
        <span className="font-semibold">{used.toLocaleString()}</span>
        <span className="text-brand-muted">
          {" "}
          / {budgetDen.toLocaleString()}
        </span>
        <span className="block text-[10px] font-normal text-brand-muted">
          est. input vs compress target (full model window up to{" "}
          {modelContextMax.toLocaleString()})
        </span>
      </p>
      <p className="mt-0.5 text-[10px] leading-snug text-brand-muted">
        {hasThreadEstimate ? (
          <>
            <span className="tabular-nums text-brand-800">
              ~{remainingBeforeCompress.toLocaleString()}
            </span>{" "}
            tokens left before compress
          </>
        ) : (
          <>Updates after each assistant reply</>
        )}
      </p>

      <div className="mt-2 border-t border-brand-border/80 pt-2">
        <div className="flex items-center justify-between gap-1">
          <span className="text-[10px] font-semibold uppercase tracking-wide text-brand-muted">
            Server (60s)
          </span>
          <span
            className={[
              "text-[10px] tabular-nums",
              tpmCrit
                ? "font-semibold text-red-800"
                : tpmWarn
                  ? "text-amber-900"
                  : "text-brand-muted",
            ].join(" ")}
          >
            {tpm.toLocaleString()} / {tpmPerMinuteGuide.toLocaleString()}
          </span>
        </div>
        <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-brand-border">
          <div
            className={`h-full rounded-full transition-all ${tpmBarColor}`}
            style={{ width: `${tpmBarPct}%` }}
          />
        </div>
        <p className="mt-1 text-[9px] leading-snug text-brand-muted">
          Input tokens recorded by this API server for your Anthropic key in the last 60 seconds
          (orchestrator process). Sub-agents run in other processes unless wired to report here.
        </p>
        {tpmWarn && !tpmCrit && (
          <p className="mt-1 text-[10px] font-medium text-amber-900">
            Approaching ~{(tpmPerMinuteGuide / 1000).toFixed(0)}k/min guide — consider pausing or
            shorter turns.
          </p>
        )}
        {tpmCrit && (
          <p className="mt-1 text-[10px] font-medium text-red-800">
            High usage vs ~{(tpmPerMinuteGuide / 1000).toFixed(0)}k/min guide — risk of rate limit;
            wait ~1 min or reduce tool-heavy requests.
          </p>
        )}
      </div>
    </div>
  );
}

/**
 * Compact bar-chart control; opens full token / TPM details on click.
 */
export function TokenBudgetPopover({
  tokenUsage,
  tpmPerMinuteGuide = DEFAULT_TPM_PER_MINUTE_GUIDE,
  tpmServerSliding60s,
  inputBudgetTarget = DEFAULT_INPUT_BUDGET_TARGET,
  modelContextMax = DEFAULT_MODEL_CONTEXT_MAX,
}: {
  tokenUsage: { tokens: number; pct: number };
  tpmPerMinuteGuide?: number;
  tpmServerSliding60s: number;
  inputBudgetTarget?: number;
  modelContextMax?: number;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const panelId = useId();

  const used = Math.max(0, tokenUsage.tokens);
  const budgetDen = Math.max(1, inputBudgetTarget);
  const budgetPct = Math.min(1, used / budgetDen);
  const tpm = Math.max(0, tpmServerSliding60s);
  const tpmWarn = tpm >= TPM_WARN * tpmPerMinuteGuide;
  const tpmCrit =
    tpm >= TPM_CRITICAL * tpmPerMinuteGuide || tpm > tpmPerMinuteGuide;
  const ctxWarn = budgetPct >= 0.55;
  const ctxCrit = budgetPct >= 0.85;
  const showAlertDot = tpmCrit || tpmWarn || ctxCrit || ctxWarn;

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

  return (
    <div ref={rootRef} className="relative shrink-0 self-end">
      <button
        type="button"
        id="token-budget-trigger"
        aria-expanded={open}
        aria-controls={panelId}
        aria-label="Token usage"
        title="Token usage (context & server 60s TPM)"
        onClick={() => setOpen((o) => !o)}
        className={[
          "relative flex h-[46px] w-[46px] shrink-0 items-center justify-center rounded-xl border text-brand-700 transition-colors",
          open
            ? "border-brand-500 bg-brand-100 shadow-inner"
            : "border-brand-border bg-white hover:border-brand-400 hover:bg-brand-50",
        ].join(" ")}
      >
        <BarChartIcon className="h-6 w-6" />
        {showAlertDot && (
          <span
            className={[
              "absolute right-1.5 top-1.5 h-2 w-2 rounded-full ring-2 ring-white",
              tpmCrit || ctxCrit ? "bg-red-500" : "bg-amber-500",
            ].join(" ")}
          />
        )}
      </button>

      {open && (
        <div
          id={panelId}
          role="region"
          aria-labelledby="token-budget-trigger"
          className="absolute bottom-full left-0 z-30 mb-2"
        >
          <div className="rounded-xl border border-brand-border/80 bg-white/95 p-1 shadow-xl backdrop-blur-sm">
            <TokenBudgetPanel
              tokenUsage={tokenUsage}
              tpmPerMinuteGuide={tpmPerMinuteGuide}
              tpmServerSliding60s={tpmServerSliding60s}
              inputBudgetTarget={inputBudgetTarget}
              modelContextMax={modelContextMax}
            />
          </div>
        </div>
      )}
    </div>
  );
}
