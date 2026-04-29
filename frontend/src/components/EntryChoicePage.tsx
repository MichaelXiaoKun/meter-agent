import AuthPageShell from "./AuthPageShell";
import BluebotWordmarkLogo from "./BluebotWordmarkLogo";

interface EntryChoicePageProps {
  onChooseAdmin: () => void;
  onChooseSales: () => void;
}

function ArrowRightIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M5 12h14" />
      <path d="m12 5 7 7-7 7" />
    </svg>
  );
}

export default function EntryChoicePage({
  onChooseAdmin,
  onChooseSales,
}: EntryChoicePageProps) {
  return (
    <AuthPageShell>
      <div className="overflow-hidden rounded-2xl border border-brand-border/80 bg-white shadow-lg shadow-slate-900/5 dark:border-brand-border dark:bg-brand-100 dark:shadow-[0_20px_50px_-20px_rgba(0,0,0,0.55)]">
        <div className="p-5 sm:p-8 md:p-10">
          <div className="mb-7 flex w-full max-w-full flex-col items-start">
            <BluebotWordmarkLogo />
            <h1 className="m-0 mt-3 w-full min-w-0 text-left text-xl font-bold leading-snug text-brand-700 sm:text-2xl">
              How can we help?
            </h1>
          </div>

          <div className="grid gap-3">
            <button
              type="button"
              onClick={onChooseSales}
              className="group flex min-h-[5rem] w-full items-center justify-between gap-4 rounded-xl border border-brand-border bg-brand-50 px-4 py-4 text-left transition hover:border-brand-500 hover:bg-white hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/40 dark:bg-brand-50"
            >
              <span className="min-w-0">
                <span className="block text-base font-bold text-brand-900">
                  FlowIQ Sales
                </span>
                <span className="mt-1 block text-sm leading-snug text-brand-muted">
                  Product fit, pipe impact, installation, and quote qualification.
                </span>
              </span>
              <ArrowRightIcon className="h-5 w-5 shrink-0 text-brand-600 transition group-hover:translate-x-0.5" />
            </button>

            <button
              type="button"
              onClick={onChooseAdmin}
              className="group flex min-h-[5rem] w-full items-center justify-between gap-4 rounded-xl border border-brand-border bg-white px-4 py-4 text-left transition hover:border-brand-500 hover:bg-brand-50 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/40 dark:bg-brand-100"
            >
              <span className="min-w-0">
                <span className="block text-base font-bold text-brand-900">
                  FlowIQ Expert
                </span>
                <span className="mt-1 block text-sm leading-snug text-brand-muted">
                  Data-backed flow insights, meter diagnostics, and expert support.
                </span>
              </span>
              <ArrowRightIcon className="h-5 w-5 shrink-0 text-brand-600 transition group-hover:translate-x-0.5" />
            </button>
          </div>
        </div>
      </div>
    </AuthPageShell>
  );
}
