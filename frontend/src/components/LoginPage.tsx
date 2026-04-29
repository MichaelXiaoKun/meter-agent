import { useEffect, useState } from "react";
import * as api from "../api";
import BluebotWordmarkLogo from "./BluebotWordmarkLogo";
import AuthPageShell from "./AuthPageShell";

interface LoginPageProps {
  /** ``persist: false`` stores the session in ``sessionStorage`` (tab-only), like SaaS without “keep me logged in”. */
  onLogin: (token: string, user: string, options?: { persist?: boolean }) => void;
  onForgotPassword: () => void;
  onBackToEntry?: () => void;
}

function EyeIcon({ show }: { show: boolean }) {
  if (show) {
    return (
      <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
        <path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z" />
        <circle cx="12" cy="12" r="3" />
      </svg>
    );
  }
  return (
    <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" />
      <line x1="1" y1="1" x2="23" y2="23" />
    </svg>
  );
}

/**
 * Sign-in view aligned with ``bluebot-saas-client`` auth layout (``AuthWrapper1``,
 * ``AuthCardWrapper``, ``AuthLogin`` + ``AuthFooter``) without
 * pulling in MUI / Next — same Vite + Tailwind stack as the rest of this app.
 */
export default function LoginPage({
  onLogin,
  onForgotPassword,
  onBackToEntry,
}: LoginPageProps) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [emailLocked, setEmailLocked] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [keepLoggedIn, setKeepLoggedIn] = useState(true);

  useEffect(() => {
    const sp = new URLSearchParams(window.location.search);
    const forced = sp.get("email");
    if (forced) {
      setEmail(forced);
      setEmailLocked(true);
    }
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!email || !password) {
      setError("Please enter both email and password.");
      return;
    }

    setError("");
    setLoading(true);
    try {
      const { access_token, user } = await api.login(email, password);
      onLogin(access_token, user, { persist: keepLoggedIn });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthPageShell>
      <div className="overflow-hidden rounded-2xl border border-brand-border/80 bg-white shadow-lg shadow-slate-900/5 dark:border-brand-border dark:bg-brand-100 dark:shadow-[0_20px_50px_-20px_rgba(0,0,0,0.55)]">
        <div className="p-5 sm:p-8 md:p-10">
              <div className="mb-6 flex w-full max-w-full flex-col items-start sm:mb-8">
                <BluebotWordmarkLogo />
                <h1 className="m-0 mt-2 w-full min-w-0 text-left text-xl font-bold leading-snug text-brand-700 sm:text-2xl">
                  LET&apos;S GET STARTED!
                </h1>
              </div>

              <form onSubmit={handleSubmit} className="space-y-5" noValidate>
                <p className="text-base font-medium text-brand-800 dark:text-brand-muted">
                  Login, welcome back!
                </p>

                <div>
                  <label
                    htmlFor="login-email"
                    className="mb-1.5 block text-sm font-medium text-brand-800 dark:text-brand-muted"
                  >
                    Email address / username
                  </label>
                  <input
                    id="login-email"
                    type="email"
                    name="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="you@example.com"
                    autoComplete="email"
                    disabled={emailLocked}
                    className="min-h-[44px] w-full rounded-xl border-[1.5px] border-brand-border bg-brand-50 px-3.5 py-2.5 text-base text-brand-900 outline-none transition-all placeholder:text-brand-muted/50 focus:border-brand-500 focus:bg-white focus:ring-2 focus:ring-brand-500/20 disabled:cursor-not-allowed disabled:opacity-80 dark:focus:bg-brand-100 sm:min-h-0 sm:text-sm"
                  />
                  {emailLocked && (
                    <p className="mt-1.5 text-xs text-brand-muted">
                      You&apos;re signing in as {email} (from link).
                    </p>
                  )}
                </div>

                <div>
                  <label
                    htmlFor="login-password"
                    className="mb-1.5 block text-sm font-medium text-brand-800 dark:text-brand-muted"
                  >
                    Password
                  </label>
                  <div className="relative">
                    <input
                      id="login-password"
                      type={showPassword ? "text" : "password"}
                      name="password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      placeholder="••••••••"
                      autoComplete="current-password"
                      className="min-h-[44px] w-full rounded-xl border-[1.5px] border-brand-border bg-brand-50 py-2.5 pl-3.5 pr-12 text-base text-brand-900 outline-none transition-all placeholder:text-brand-muted/50 focus:border-brand-500 focus:bg-white focus:ring-2 focus:ring-brand-500/20 dark:focus:bg-brand-100 sm:min-h-0 sm:text-sm"
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword((s) => !s)}
                      className="absolute right-2 top-1/2 -translate-y-1/2 rounded-lg p-2 text-brand-600 transition-colors hover:bg-brand-200/50 dark:text-brand-400 dark:hover:bg-brand-300/20"
                      aria-label={showPassword ? "Hide password" : "Show password"}
                    >
                      <EyeIcon show={showPassword} />
                    </button>
                  </div>
                </div>

                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <label className="inline-flex cursor-pointer select-none items-center gap-2 text-sm text-brand-800 dark:text-brand-muted">
                    <input
                      type="checkbox"
                      checked={keepLoggedIn}
                      onChange={(e) => setKeepLoggedIn(e.target.checked)}
                      className="h-4 w-4 rounded border-brand-border text-brand-700 focus:ring-brand-500"
                    />
                    Keep me logged in
                  </label>
                  <button
                    type="button"
                    onClick={onForgotPassword}
                    className="self-start text-sm font-medium text-brand-700 underline-offset-2 hover:underline sm:self-auto dark:text-brand-500"
                  >
                    Forgot password?
                  </button>
                </div>

                {error && (
                  <div
                    className="rounded-lg border border-red-200/80 bg-red-50 px-3 py-2.5 text-sm text-red-700 dark:border-red-900/50 dark:bg-red-950/40 dark:text-red-200"
                    role="alert"
                  >
                    {error}
                  </div>
                )}

                <div className="pt-1">
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
                    {loading ? "Signing in…" : "Login"}
                  </button>
                </div>
              </form>
              {onBackToEntry && (
                <button
                  type="button"
                  onClick={onBackToEntry}
                  className="mt-5 text-sm font-medium text-brand-muted underline-offset-2 hover:text-brand-700 hover:underline"
                >
                  Back to options
                </button>
              )}
        </div>
      </div>
    </AuthPageShell>
  );
}
