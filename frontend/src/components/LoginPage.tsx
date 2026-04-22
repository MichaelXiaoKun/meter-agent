import { useState } from "react";
import * as api from "../api";
import ThemeToggle from "./ThemeToggle";

interface LoginPageProps {
  onLogin: (token: string, user: string) => void;
}

export default function LoginPage({ onLogin }: LoginPageProps) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

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
      onLogin(access_token, user);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="relative flex min-h-[100dvh] items-center justify-center bg-gradient-to-br from-[#e8f0fb] via-[#dce7f8] to-[#cfddf6] px-4 pb-[max(1.5rem,env(safe-area-inset-bottom,0px))] pt-[max(1.5rem,env(safe-area-inset-top,0px))] dark:from-brand-100 dark:via-brand-50 dark:to-brand-50">
      <div className="absolute right-4 top-4 z-10 sm:right-6 sm:top-6">
        <ThemeToggle />
      </div>
      <div className="w-full max-w-sm rounded-2xl border border-transparent bg-white px-6 pt-10 pb-8 shadow-lg shadow-brand-700/10 dark:border-brand-border dark:shadow-[0_20px_50px_-20px_rgba(0,0,0,0.55)] sm:px-8">
        {/* Logo + title */}
        <div className="mb-8 text-center">
          <img
            src="/api/logo"
            alt="bluebot"
            className="mx-auto mb-4 h-20 w-20 rounded-2xl object-cover shadow-md shadow-brand-700/20"
          />
          <h1 className="text-2xl font-bold tracking-tight text-brand-900">
            bluebot Assistant
          </h1>
          <p className="mt-1 text-sm text-brand-muted">
            Sign in to your account to continue
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="mb-1 block text-sm font-medium text-brand-800 dark:text-brand-muted">
              Email
            </label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              autoComplete="email"
              className="min-h-[44px] w-full rounded-xl border-[1.5px] border-brand-border bg-brand-50 px-3.5 py-2.5 text-base text-brand-900 outline-none transition-all placeholder:text-brand-muted/50 focus:border-brand-500 focus:bg-white focus:ring-3 focus:ring-brand-500/15 dark:focus:bg-brand-100 sm:min-h-0 sm:text-sm"
            />
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium text-brand-800 dark:text-brand-muted">
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              autoComplete="current-password"
              className="min-h-[44px] w-full rounded-xl border-[1.5px] border-brand-border bg-brand-50 px-3.5 py-2.5 text-base text-brand-900 outline-none transition-all placeholder:text-brand-muted/50 focus:border-brand-500 focus:bg-white focus:ring-3 focus:ring-brand-500/15 dark:focus:bg-brand-100 sm:min-h-0 sm:text-sm"
            />
          </div>

          {error && (
            <div className="rounded-lg border border-red-200/80 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900/50 dark:bg-red-950/40 dark:text-red-200">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="min-h-[44px] w-full rounded-xl bg-gradient-to-br from-brand-700 to-brand-500 py-2.5 text-base font-semibold text-white shadow-sm transition-all hover:opacity-90 hover:shadow-md active:opacity-90 disabled:opacity-60 sm:min-h-0 sm:text-sm"
          >
            {loading ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}
