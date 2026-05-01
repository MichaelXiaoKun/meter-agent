import BluebotWordmarkLogo from "../../branding/components/BluebotWordmarkLogo";
import AuthPageShell from "./AuthPageShell";

type CheckMailPageProps = {
  onBackToLogin: () => void;
};

/**
 * ``bluebot-saas-client`` ``check-mail`` — confirmation after Auth0 sent reset email.
 */
export default function CheckMailPage({ onBackToLogin }: CheckMailPageProps) {
  return (
    <AuthPageShell>
      <div className="overflow-hidden rounded-2xl border border-brand-border/80 bg-white shadow-lg shadow-slate-900/5 dark:border-brand-border dark:bg-brand-100 dark:shadow-[0_20px_50px_-20px_rgba(0,0,0,0.55)]">
        <div className="p-5 sm:p-8 md:p-10">
          <div className="mb-6 flex w-full max-w-full flex-col items-center sm:mb-8">
            <a href="#" aria-label="bluebot" className="inline-flex" onClick={(e) => e.preventDefault()}>
              <BluebotWordmarkLogo />
            </a>
            <h1 className="m-0 mt-6 w-full text-center text-2xl font-bold leading-tight text-brand-500 dark:text-brand-500 sm:mt-8 sm:text-3xl">
              Hi, Check Your Mail
            </h1>
            <p className="m-0 mt-3 w-full text-center text-base leading-snug text-brand-800 dark:text-brand-muted">
              We have sent a password recover instructions to your email.
            </p>
          </div>

          <button
            type="button"
            onClick={onBackToLogin}
            className="min-h-[48px] w-full rounded-xl bg-gradient-to-br from-brand-700 to-brand-500 py-3 text-sm font-bold uppercase tracking-wide text-white shadow-sm transition-all hover:opacity-90 hover:shadow-md active:opacity-90"
          >
            Back to Login
          </button>
        </div>
      </div>
    </AuthPageShell>
  );
}
