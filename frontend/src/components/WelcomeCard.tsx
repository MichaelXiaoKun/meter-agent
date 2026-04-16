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

function ChevronIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M9 18l6-6-6-6" />
    </svg>
  );
}

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

  function ActionList({ actions }: { actions: QuickAction[] }) {
    return (
      <ul className="space-y-2" role="list">
        {actions.map((a) => (
          <li key={a.id}>
            <button
              type="button"
              onClick={() => runAction(a.message)}
              className="group flex w-full items-start gap-3 rounded-xl border border-brand-border bg-white/90 px-4 py-3.5 text-left shadow-sm transition-all hover:border-brand-500/80 hover:bg-white hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2"
            >
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-medium text-brand-900">{a.title}</span>
                <span className="mt-0.5 block text-xs leading-snug text-brand-muted">
                  {a.subtitle}
                </span>
              </span>
              <ChevronIcon className="mt-1 shrink-0 text-brand-500 opacity-70 transition group-hover:translate-x-0.5 group-hover:opacity-100" />
            </button>
          </li>
        ))}
      </ul>
    );
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-10 md:py-14">
      <div className="rounded-2xl border border-brand-border/90 bg-gradient-to-b from-white via-brand-50/40 to-brand-50/90 p-8 shadow-sm md:p-10">
        <p className="text-center text-[11px] font-semibold uppercase tracking-widest text-brand-500">
          Getting started
        </p>
        <h2 className="mt-2 text-center text-xl font-bold tracking-tight text-brand-900 md:text-2xl">
          What do you want to check today?
        </h2>
        <p className="mx-auto mt-3 max-w-xl text-center text-sm leading-relaxed text-brand-muted">
          Enter your meter serial once, then use a shortcut — or type anything in the box below.
        </p>

        <div
          className={[
            "mt-8 rounded-2xl border bg-white/90 p-4 shadow-sm transition-colors md:p-5",
            serialError
              ? "border-amber-300 ring-2 ring-amber-100"
              : "border-brand-border/90",
          ].join(" ")}
        >
          <label
            htmlFor={serialId}
            className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-brand-muted"
          >
            <span
              className="flex h-6 w-6 items-center justify-center rounded-lg bg-brand-100 text-[13px] text-brand-700"
              aria-hidden
            >
              #
            </span>
            Meter serial
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
            className="mt-3 w-full rounded-xl border border-brand-border bg-white px-4 py-3 text-sm font-medium text-brand-900 outline-none transition placeholder:text-brand-muted/50 focus:border-brand-500 focus:ring-2 focus:ring-brand-500/15"
          />
          {serialError ? (
            <p className="mt-2 text-xs font-medium text-amber-800" role="status">
              Add a serial number to use a shortcut.
            </p>
          ) : (
            <p className="mt-2 text-xs text-brand-muted">
              Same string you use in API paths — we insert it into your message automatically.
            </p>
          )}
        </div>

        <div className="mt-10 space-y-10">
          <section aria-labelledby="welcome-status-flow">
            <div className="mb-3 flex flex-col gap-1 border-b border-brand-border/80 pb-3 md:flex-row md:items-end md:justify-between">
              <h3 id="welcome-status-flow" className="text-sm font-semibold text-brand-900">
                Status &amp; flow
              </h3>
              <p className="text-xs leading-snug text-brand-muted md:max-w-[55%] md:text-right">
                Diagnostics and historical flow for that meter.
              </p>
            </div>
            <ActionList actions={STATUS_ACTIONS} />
          </section>

          <section aria-labelledby="welcome-pipe">
            <div className="mb-3 flex flex-col gap-1 border-b border-brand-border/80 pb-3 md:flex-row md:items-end md:justify-between">
              <h3 id="welcome-pipe" className="text-sm font-semibold text-brand-900">
                Pipe &amp; angle
              </h3>
              <p className="text-xs leading-snug text-brand-muted md:max-w-[55%] md:text-right">
                Management + MQTT — confirm details in chat before sending.
              </p>
            </div>
            <ActionList actions={PIPE_ACTIONS} />
          </section>
        </div>

        <p className="mt-10 text-center text-xs text-brand-muted/90">
          Tip: for ranges, say things like &ldquo;last 6 hours&rdquo; or your timezone — time is
          resolved before flow analysis runs.
        </p>
      </div>
    </div>
  );
}
