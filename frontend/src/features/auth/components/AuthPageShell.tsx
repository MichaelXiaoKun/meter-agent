import { type ReactNode } from "react";
import ThemeToggle from "../../theme/components/ThemeToggle";

type AuthPageShellProps = {
  children: ReactNode;
};

/**
 * Shared auth layout: matches ``AuthWrapper1`` (grey / dark full-height bg) + centered column + footer,
 * used by login, forgot password, and check-mail screens.
 */
export default function AuthPageShell({ children }: AuthPageShellProps) {
  return (
    <div className="relative flex min-h-[100dvh] flex-col bg-[#eef2f6] text-brand-900 dark:bg-[#111936] dark:text-brand-900">
      <div className="absolute right-4 top-4 z-10 sm:right-6 sm:top-6">
        <ThemeToggle />
      </div>

      <div className="flex min-h-0 flex-1 flex-col justify-center py-[max(1rem,env(safe-area-inset-top,0px))] pb-[max(1rem,env(safe-area-inset-bottom,0px))]">
        <div className="mx-auto w-full max-w-[475px] px-4 sm:px-0">{children}</div>
      </div>

      <footer className="mt-auto flex shrink-0 flex-wrap items-center justify-between gap-2 px-4 py-3 text-sm text-brand-muted sm:px-6 md:px-8">
        <a
          href="https://bluebot.com"
          target="_blank"
          rel="noreferrer"
          className="underline-offset-2 hover:underline"
        >
          bluebot.com
        </a>
        <a
          href="https://bluebot.com"
          target="_blank"
          rel="noreferrer"
          className="underline-offset-2 hover:underline"
        >
          © bluebot.com
        </a>
      </footer>
    </div>
  );
}
