import { useEffect, useState } from "react";
import * as api from "../../../api/client";
import BluebotWordmarkLogo from "../../branding/components/BluebotWordmarkLogo";
import AuthPageShell from "./AuthPageShell";

const EMAIL_ERR = "Must be a valid email";

function isValidEmail(s: string) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(s.trim());
}

type ForgotPasswordPageProps = {
  onBackToLogin: () => void;
  onSuccess: () => void;
};

/**
 * ``bluebot-saas-client`` ``forgot-password`` + ``AuthForgotPassword`` (no MUI) —
 * “Forgot password?”, subtitle, one field, “Send Mail”, divider, “Already have an account?”.
 */
export default function ForgotPasswordPage({ onBackToLogin, onSuccess }: ForgotPasswordPageProps) {
  const [email, setEmail] = useState("");
  const [touched, setTouched] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [loading, setLoading] = useState(false);

  const emailError = touched && !email.trim() ? "Email is required" : touched && !isValidEmail(email) ? EMAIL_ERR : "";

  useEffect(() => {
    const sp = new URLSearchParams(window.location.search);
    const e = sp.get("email");
    if (e) setEmail(e);
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (!email.trim() || !isValidEmail(email)) {
      return;
    }
    setSubmitError("");
    setLoading(true);
    try {
      await api.requestPasswordReset(email.trim());
      await new Promise((r) => setTimeout(r, 1500));
      onSuccess();
    } catch (err) {
      setSubmitError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthPageShell>
      <div className="overflow-hidden rounded-2xl border border-brand-border/80 bg-white shadow-lg shadow-slate-900/5 dark:border-brand-border dark:bg-brand-100 dark:shadow-[0_20px_50px_-20px_rgba(0,0,0,0.55)]">
        <div className="p-5 sm:p-8 md:p-10">
          <div className="mb-6 flex w-full max-w-full flex-col items-center sm:mb-8">
            <a href="#" aria-label="bluebot" className="inline-flex" onClick={(e) => e.preventDefault()}>
              <BluebotWordmarkLogo />
            </a>
            <h1 className="m-0 mt-6 w-full text-center text-2xl font-bold leading-tight text-brand-500 dark:text-brand-500 sm:mt-8 sm:text-3xl">
              Forgot password?
            </h1>
            <p className="m-0 mt-3 w-full text-center text-base leading-snug text-brand-800 dark:text-brand-muted">
              Enter your email address below and we&apos;ll send you password reset OTP.
            </p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4" noValidate>
            <div>
              <label
                htmlFor="forgot-email"
                className="mb-1.5 block text-sm font-medium text-brand-800 dark:text-brand-muted"
              >
                Email Address / Username
              </label>
              <input
                id="forgot-email"
                type="email"
                name="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                onBlur={() => setTouched(true)}
                autoComplete="email"
                className="min-h-[44px] w-full rounded-xl border-[1.5px] border-brand-border bg-brand-50 px-3.5 py-2.5 text-base text-brand-900 outline-none transition-all placeholder:text-brand-muted/50 focus:border-brand-500 focus:bg-white focus:ring-2 focus:ring-brand-500/20 dark:focus:bg-brand-100 sm:min-h-0 sm:text-sm"
              />
              {emailError && (
                <p className="mt-1.5 text-sm text-red-600 dark:text-red-400" id="forgot-email-error" role="alert">
                  {emailError}
                </p>
              )}
            </div>

            {submitError && (
              <p className="m-0 text-sm text-red-600 dark:text-red-400" role="alert">
                {submitError}
              </p>
            )}

            <div className="pt-0">
              {loading && (
                <div className="mb-1 h-0.5 w-full overflow-hidden rounded-full bg-brand-200">
                  <div className="h-full w-1/2 animate-pulse bg-brand-500" />
                </div>
              )}
              <button
                type="submit"
                disabled={loading}
                className="min-h-[48px] w-full rounded-xl bg-gradient-to-br from-brand-700 to-brand-500 py-3 text-sm font-bold uppercase tracking-wide text-white shadow-sm transition-all hover:opacity-90 hover:shadow-md active:opacity-90 disabled:opacity-60"
              >
                {loading ? "Sending…" : "Send Mail"}
              </button>
            </div>
          </form>

          <div
            className="my-6 w-full border-t border-brand-border/80 dark:border-brand-border/60"
            role="separator"
          />

          <p className="m-0 text-center text-sm font-medium text-brand-700 dark:text-brand-500">
            <button
              type="button"
              onClick={onBackToLogin}
              className="border-none bg-transparent p-0 text-inherit underline-offset-2 hover:underline"
            >
              Already have an account?
            </button>
          </p>
        </div>
      </div>
    </AuthPageShell>
  );
}
