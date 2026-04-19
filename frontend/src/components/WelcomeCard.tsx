import { useEffect, useId, useRef, useState } from "react";

function readStoredSerial(): string {
  try {
    return sessionStorage.getItem("bb_welcome_serial") ?? "";
  } catch {
    return "";
  }
}

type QuickAction = {
  id: string;
  title: string;
  subtitle: string;
  message: (serial: string) => string;
};

const STATUS_ACTIONS: QuickAction[] = [
  {
    id: "health",
    title: "Health check",
    subtitle: "Status, signal quality, pipe snapshot",
    message: (s) => `Run a health check on meter ${s}`,
  },
  {
    id: "flow-7d",
    title: "Flow analysis — last 7 days",
    subtitle: "Trends, gaps, and quality over a week",
    message: (s) => `Analyse the last 7 days of flow data for meter ${s}`,
  },
  {
    id: "online",
    title: "Online & transmitting?",
    subtitle: "Quick connectivity check",
    message: (s) => `Is meter ${s} online and transmitting?`,
  },
];

const PIPE_ACTIONS: QuickAction[] = [
  {
    id: "pipe-full",
    title: "Configure pipe + angle",
    subtitle: "PVC Sch 40, 2″, 45° — edit in chat if needed",
    message: (s) =>
      `Configure pipe for serial ${s}: PVC, Schedule 40, 2 inch nominal, transducer angle 45º`,
  },
  {
    id: "angle-only",
    title: "Transducer angle only",
    subtitle: "SSA update without changing pipe catalog",
    message: (s) =>
      `Set transducer angle only for serial ${s} to 35º (no pipe size change)`,
  },
];

interface WelcomeCardProps {
  /** Full message ready to send (serial already interpolated). */
  onCompose: (message: string) => void;
}

export default function WelcomeCard({ onCompose }: WelcomeCardProps) {
  const serialId = useId();
  const serialInputRef = useRef<HTMLInputElement>(null);
  const [serial, setSerial] = useState(readStoredSerial);
  const [serialError, setSerialError] = useState(false);

  useEffect(() => {
    try {
      const t = serial.trim();
      if (t) sessionStorage.setItem("bb_welcome_serial", t);
    } catch {
      /* ignore */
    }
  }, [serial]);

  function runAction(build: (s: string) => string) {
    const t = serial.trim();
    if (!t) {
      setSerialError(true);
      serialInputRef.current?.focus();
      return;
    }
    setSerialError(false);
    onCompose(build(t));
  }

  function ActionGrid({ actions }: { actions: QuickAction[] }) {
    return (
      <ul className="grid gap-3.5 sm:grid-cols-2 sm:gap-3" role="list">
        {actions.map((a) => (
          <li key={a.id}>
            <button
              type="button"
              onClick={() => runAction(a.message)}
              className="group flex h-full min-h-[4.75rem] w-full flex-col items-start rounded-2xl border border-slate-200/90 bg-white px-4 py-4 text-left shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition hover:border-slate-300 hover:bg-slate-50/80 active:bg-slate-100/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 sm:min-h-[4.25rem] sm:py-3"
            >
              <span className="text-base font-medium text-brand-900 sm:text-sm">{a.title}</span>
              <span className="mt-1 line-clamp-2 text-sm leading-snug text-brand-muted sm:text-xs">
                {a.subtitle}
              </span>
            </button>
          </li>
        ))}
      </ul>
    );
  }

  return (
    <div className="w-full">
      <p className="text-center text-[0.6875rem] font-medium uppercase tracking-wider text-brand-muted sm:text-xs">
        Suggestions
      </p>

      <div
        className={[
          "mx-auto mt-4 max-w-md rounded-2xl border bg-white px-4 py-4 shadow-sm transition-colors sm:py-3",
          serialError
            ? "border-amber-300 ring-2 ring-amber-100"
            : "border-slate-200/90",
        ].join(" ")}
      >
        <label
          htmlFor={serialId}
          className="text-[0.8125rem] font-medium text-brand-muted sm:text-xs"
        >
          Meter serial for shortcuts
        </label>
        <input
          id={serialId}
          ref={serialInputRef}
          type="text"
          autoComplete="off"
          spellCheck={false}
          placeholder="e.g. BB8100015261"
          value={serial}
          onChange={(e) => {
            setSerial(e.target.value);
            setSerialError(false);
          }}
          className="mt-2 min-h-[44px] w-full rounded-xl border border-slate-200 bg-slate-50/80 px-3 py-2.5 text-base text-brand-900 outline-none transition placeholder:text-brand-muted/45 focus:border-brand-500 focus:bg-white focus:ring-2 focus:ring-brand-500/15 sm:min-h-0 sm:text-sm"
          inputMode="text"
        />
        {serialError ? (
          <p className="mt-2 text-xs font-medium text-amber-800" role="status">
            Add a serial to use a suggestion.
          </p>
        ) : (
          <p className="mt-2 text-xs text-brand-muted">
            We fill this into the message when you tap a card below.
          </p>
        )}
      </div>

      <div className="mt-6 space-y-7 sm:mt-8 sm:space-y-8">
        <section aria-labelledby="welcome-status-flow">
          <h3
            id="welcome-status-flow"
            className="mb-3 text-base font-semibold text-brand-900 sm:text-sm"
          >
            Status &amp; flow
          </h3>
          <ActionGrid actions={STATUS_ACTIONS} />
        </section>

        <section aria-labelledby="welcome-pipe">
          <h3 id="welcome-pipe" className="mb-3 text-base font-semibold text-brand-900 sm:text-sm">
            Pipe &amp; angle
          </h3>
          <ActionGrid actions={PIPE_ACTIONS} />
        </section>
      </div>

      <p className="mt-6 px-1 text-center text-sm leading-relaxed text-brand-muted sm:mt-8 sm:px-0 sm:text-xs">
        Tip: say &ldquo;last 6 hours&rdquo; or your timezone — time is resolved before flow analysis runs.
      </p>
    </div>
  );
}
