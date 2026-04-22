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
  const ctxPctRounded =
    modelPctDisplay < 10 ? modelPctDisplay.toFixed(1) : modelPctDisplay.toFixed(0);

  return (
    <div
      className={[
        "flex w-[min(100vw-1.5rem,20rem)] flex-col justify-center rounded-xl border px-3 py-2.5 shadow-lg transition-colors",
        tpmCrit
          ? "border-red-300 bg-red-50/95 dark:border-red-900/55 dark:bg-red-950/40 dark:shadow-[0_12px_40px_-16px_rgba(0,0,0,0.5)]"
          : tpmWarn
            ? "border-amber-300 bg-amber-50/90 dark:border-amber-800/50 dark:bg-amber-950/35 dark:shadow-[0_12px_40px_-16px_rgba(0,0,0,0.45)]"
            : hasAnySignal
              ? "border-brand-border bg-white dark:border-brand-border dark:bg-brand-100 dark:shadow-[0_12px_40px_-16px_rgba(0,0,0,0.45)]"
              : "border-brand-border/90 bg-brand-50/80 dark:border-brand-border/80 dark:bg-brand-50/90 dark:shadow-[0_12px_40px_-16px_rgba(0,0,0,0.4)]",
      ].join(" ")}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-brand-muted">
          Model context
        </span>
        <span className="text-right text-xs text-brand-muted tabular-nums">
          {hasThreadEstimate ? (
            <>
              <span className="font-semibold text-brand-900">~{ctxPctRounded}%</span>
              <span className="block text-[11px] font-normal text-brand-muted/90">
                of {formatCompactTokens(inputBudgetTarget)} target
              </span>
            </>
          ) : (
            <span className="text-[11px]">No estimate yet</span>
          )}
        </span>
      </div>
      <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-slate-200/90 dark:bg-brand-border/50">
        <div
          className={`h-full rounded-full transition-all ${barColor}`}
          style={{ width: `${ctxBarPct}%` }}
        />
      </div>
      <p className="mt-2 text-sm leading-snug text-brand-900 tabular-nums">
        <span className="font-semibold">{used.toLocaleString()}</span>
        <span className="text-brand-muted">
          {" "}
          / {budgetDen.toLocaleString()}
        </span>
        <span className="mt-1 block text-xs font-normal leading-snug text-brand-muted">
          Estimated input tokens vs compress target. Full model window up to{" "}
          <span className="tabular-nums">{modelContextMax.toLocaleString()}</span>.
        </span>
      </p>
      <p className="mt-2 text-xs leading-snug text-brand-muted">
        {hasThreadEstimate ? (
          <>
            <span className="font-medium tabular-nums text-brand-800">
              ~{remainingBeforeCompress.toLocaleString()}
            </span>{" "}
            tokens left before compress
          </>
        ) : (
          <>Updates after each assistant reply.</>
        )}
      </p>

      <div className="mt-3 border-t border-brand-border/80 pt-3">
        <div className="flex items-start justify-between gap-2">
          <div>
            <span className="text-xs font-semibold uppercase tracking-wide text-brand-muted">
              Rate limit (60s)
            </span>
            <p className="mt-0.5 text-[11px] leading-snug text-brand-muted">
              Input TPM on this server (your key), rolling 60s window
            </p>
          </div>
          <span
            className={[
              "shrink-0 text-right text-xs tabular-nums",
              tpmCrit
                ? "font-semibold text-red-800 dark:text-red-200"
                : tpmWarn
                  ? "font-medium text-amber-900 dark:text-amber-200"
                  : "text-brand-muted",
            ].join(" ")}
          >
            {tpm.toLocaleString()}
            <span className="text-brand-muted/90"> / {tpmPerMinuteGuide.toLocaleString()}</span>
          </span>
        </div>
        <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-slate-200/90 dark:bg-brand-border/50">
          <div
            className={`h-full rounded-full transition-all ${tpmBarColor}`}
            style={{ width: `${tpmBarPct}%` }}
          />
        </div>
        <p className="mt-2 text-[11px] leading-relaxed text-brand-muted">
          Compared to the configured per-minute guide (ITPM-style). Other processes may use the same
          API key separately.
        </p>
        {tpmWarn && !tpmCrit && (
          <p className="mt-2 text-xs font-medium text-amber-900 dark:text-amber-200/95">
            Approaching ~{(tpmPerMinuteGuide / 1000).toFixed(0)}k/min guide — consider pausing or
            shorter turns.
          </p>
        )}
        {tpmCrit && (
          <p className="mt-2 text-xs font-medium text-red-800 dark:text-red-200/95">
            High usage vs ~{(tpmPerMinuteGuide / 1000).toFixed(0)}k/min guide — risk of rate limit;
            wait ~1 min or reduce tool-heavy requests.
          </p>
        )}
      </div>
    </div>
  );
}

/** Animated placeholder on welcome when no usage yet (no static arcs to draw). */
function WelcomeIdleSpinRing() {
  return (
    <>
      <span
        className="pointer-events-none absolute inset-0 rounded-full border-[2.2px] border-slate-200 border-t-brand-500 border-r-brand-400/35 animate-spin motion-reduce:animate-none dark:border-white/[0.09] dark:border-t-brand-500/75 dark:border-r-brand-500/35"
        style={{ animationDuration: "4s" }}
        aria-hidden
      />
      <span
        className="pointer-events-none absolute inset-[5px] rounded-full border-[1.85px] border-slate-200 border-t-brand-500/75 border-b-brand-400/20 animate-spin motion-reduce:animate-none dark:border-white/[0.08] dark:border-t-brand-500/65 dark:border-b-brand-500/40"
        style={{ animationDuration: "3.2s", animationDirection: "reverse" }}
        aria-hidden
      />
    </>
  );
}

/** Dual concentric rings: outer = model context %, inner = 60s TPM vs guide (same idea as panel bars). */
function UsageGaugeRings({
  contextFill,
  tpmFill,
  ctxStrokeClass,
  tpmStrokeClass,
}: {
  contextFill: number;
  tpmFill: number;
  ctxStrokeClass: string;
  tpmStrokeClass: string;
}) {
  /* Outer = context; inner = TPM. Inner radius must sit outside the center icon disk or the disk hides the TPM arc. */
  const ro = 19;
  const ri = 15.75;
  const wo = 2.2;
  const wi = 1.85;
  const co = 2 * Math.PI * ro;
  const ci = 2 * Math.PI * ri;
  const cx = 24;
  const cy = 24;

  return (
    <svg
      className="pointer-events-none absolute inset-0 h-full w-full"
      viewBox="0 0 48 48"
      fill="none"
      aria-hidden
    >
      <circle
        cx={cx}
        cy={cy}
        r={ro}
        fill="none"
        className="stroke-slate-200/95 dark:stroke-white/[0.1]"
        strokeWidth={wo}
      />
      <circle
        cx={cx}
        cy={cy}
        r={ro}
        fill="none"
        strokeLinecap="round"
        strokeWidth={wo}
        className={["transition-[stroke-dashoffset] duration-300 ease-out", ctxStrokeClass].join(
          " "
        )}
        strokeDasharray={co}
        strokeDashoffset={co * (1 - contextFill)}
        transform={`rotate(-90 ${cx} ${cy})`}
      />
      <circle
        cx={cx}
        cy={cy}
        r={ri}
        fill="none"
        className="stroke-slate-200/80 dark:stroke-white/[0.09]"
        strokeWidth={wi}
      />
      <circle
        cx={cx}
        cy={cy}
        r={ri}
        fill="none"
        strokeLinecap="round"
        strokeWidth={wi}
        className={["transition-[stroke-dashoffset] duration-300 ease-out", tpmStrokeClass].join(
          " "
        )}
        strokeDasharray={ci}
        strokeDashoffset={ci * (1 - tpmFill)}
        transform={`rotate(-90 ${cx} ${cy})`}
      />
    </svg>
  );
}

/**
 * Icon + dual usage rings (context + TPM); opens full details on click (hover title for summary).
 */
export function TokenBudgetPopover({
  tokenUsage,
  tpmPerMinuteGuide = DEFAULT_TPM_PER_MINUTE_GUIDE,
  tpmServerSliding60s,
  inputBudgetTarget = DEFAULT_INPUT_BUDGET_TARGET,
  modelContextMax = DEFAULT_MODEL_CONTEXT_MAX,
  className,
  /** Open the panel below the trigger (e.g. welcome composer) instead of above the footer. */
  panelPlacement = "above",
  /**
   * Welcome composer only: show a subtle circling ring when there is no context or TPM yet
   * (static usage arcs would both be empty).
   */
  welcomeIdleSpin = false,
}: {
  tokenUsage: { tokens: number; pct: number };
  tpmPerMinuteGuide?: number;
  tpmServerSliding60s: number;
  inputBudgetTarget?: number;
  modelContextMax?: number;
  /** Merged onto the trigger root (e.g. align in flex layouts). */
  className?: string;
  panelPlacement?: "above" | "below";
  welcomeIdleSpin?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const panelId = useId();

  const used = Math.max(0, tokenUsage.tokens);
  const budgetDen = Math.max(1, inputBudgetTarget);
  const budgetPct = Math.min(1, used / budgetDen);
  const modelPctDisplay = budgetPct * 100;
  const hasThreadEstimate = used > 0 || budgetPct > 0;
  const tpm = Math.max(0, tpmServerSliding60s);
  const tpmWarn = tpm >= TPM_WARN * tpmPerMinuteGuide;
  const tpmCrit =
    tpm >= TPM_CRITICAL * tpmPerMinuteGuide || tpm > tpmPerMinuteGuide;

  const ctxSummary = hasThreadEstimate
    ? `~${modelPctDisplay < 10 ? modelPctDisplay.toFixed(1) : modelPctDisplay.toFixed(0)}%`
    : "—";
  const tpmSummary = `${formatCompactTokens(tpm)} / ${formatCompactTokens(tpmPerMinuteGuide)}`;

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

  /*
   * Panel positioning — always left-aligned to the trigger so the panel
   * unfurls to the right and stays within the viewport, even when the
   * trigger sits near the left edge of the composer (the previous
   * ``-translate-x-1/2`` centering cut the panel off-screen on narrow
   * phones — see the composer footer at the bottom of the mobile
   * viewport). The ``max-w`` clamp keeps the panel from overflowing
   * the opposite edge on wider triggers.
   */
  const panelPositionClass =
    panelPlacement === "below"
      ? "left-0 top-full z-40 mt-2 w-[min(20rem,calc(100vw-1rem))] max-w-[calc(100vw-env(safe-area-inset-left)-env(safe-area-inset-right)-0.5rem)] sm:w-auto sm:max-w-[calc(100vw-1.5rem)]"
      : "bottom-full left-0 z-40 mb-2 w-[min(20rem,calc(100vw-1rem))] max-w-[calc(100vw-env(safe-area-inset-left)-env(safe-area-inset-right)-0.5rem)] sm:w-auto sm:max-w-[calc(100vw-1.5rem)]";

  const tpmFill = Math.min(1, tpm / Math.max(1, tpmPerMinuteGuide));
  const ctxStrokeClass =
    budgetPct >= 0.85
      ? "stroke-red-500 dark:stroke-red-400"
      : budgetPct >= 0.55
        ? "stroke-amber-500 dark:stroke-amber-400"
        : "stroke-brand-500 dark:stroke-brand-700";
  const tpmStrokeClass = tpmCrit
    ? "stroke-red-500 dark:stroke-red-400"
    : tpmWarn
      ? "stroke-amber-500 dark:stroke-amber-400"
      : "stroke-brand-500 dark:stroke-brand-700";

  const showWelcomeIdleSpin =
    welcomeIdleSpin && used <= 0 && tpm <= 0;

  return (
    <div
      ref={rootRef}
      className={["relative shrink-0 self-center", className].filter(Boolean).join(" ")}
    >
      <button
        type="button"
        id="token-budget-trigger"
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={`Token usage: context ${ctxSummary}, 60s TPM ${tpmSummary}. Open details.`}
        title={
          showWelcomeIdleSpin
            ? "No usage yet — rings will show context and 60s TPM after you chat. Click for details."
            : `Context ${ctxSummary} · 60s TPM ${tpmSummary} — outer ring = context, inner = 60s TPM (click for full breakdown)`
        }
        onClick={() => setOpen((o) => !o)}
        className={[
          "relative flex h-12 w-12 shrink-0 items-center justify-center overflow-hidden rounded-full border p-0 text-brand-700 transition-colors dark:text-brand-muted dark:hover:text-brand-900",
          open
            ? "border-brand-500 bg-brand-50/90 shadow-inner ring-2 ring-brand-400/30 dark:border-brand-border dark:bg-brand-100 dark:shadow-inner dark:ring-1 dark:ring-white/12"
            : "border-brand-border bg-white hover:border-brand-400 hover:bg-brand-50/80 dark:bg-brand-100 dark:hover:bg-white/10",
        ].join(" ")}
      >
        {showWelcomeIdleSpin ? (
          <WelcomeIdleSpinRing />
        ) : (
          <UsageGaugeRings
            contextFill={budgetPct}
            tpmFill={tpmFill}
            ctxStrokeClass={ctxStrokeClass}
            tpmStrokeClass={tpmStrokeClass}
          />
        )}
        <span className="relative z-10 flex h-4 w-4 items-center justify-center rounded-full bg-white/95 shadow-sm ring-1 ring-brand-border/40 dark:bg-brand-50/90 dark:ring-white/12">
          <BarChartIcon className="h-3 w-3" />
        </span>
      </button>

      {open && (
        <div
          id={panelId}
          role="region"
          aria-labelledby="token-budget-trigger"
          className={`absolute ${panelPositionClass}`}
        >
          <div className="max-h-[min(70vh,32rem)] overflow-y-auto rounded-xl border border-brand-border/80 bg-white p-1 shadow-xl backdrop-blur-sm dark:border-brand-border dark:bg-brand-100 dark:shadow-[0_20px_50px_-20px_rgba(0,0,0,0.55)]">
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
