import { useTheme } from "../context/useTheme";

function IconMoon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"
      />
    </svg>
  );
}

function IconSun({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"
      />
    </svg>
  );
}

type ThemeToggleProps = {
  /** ``sm`` matches rail tiles (h-9 w-9). */
  size?: "sm" | "md";
  className?: string;
};

export default function ThemeToggle({ size = "md", className = "" }: ThemeToggleProps) {
  const { theme, toggleTheme } = useTheme();
  const isDark = theme === "dark";
  const sm = size === "sm";

  return (
    <button
      type="button"
      onClick={toggleTheme}
      className={`inline-flex shrink-0 items-center justify-center rounded-xl border border-brand-border/80 bg-white text-brand-700 shadow-sm ring-1 ring-brand-border/40 transition-colors hover:border-brand-400 hover:bg-brand-50 dark:bg-brand-100 dark:text-brand-muted dark:hover:border-brand-border dark:hover:bg-white/10 dark:hover:text-brand-900 ${sm ? "h-9 w-9" : "h-10 w-10"} ${className}`}
      title={isDark ? "Switch to light mode" : "Switch to dark mode"}
      aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
    >
      {isDark ? <IconSun className="h-5 w-5" /> : <IconMoon className="h-5 w-5" />}
    </button>
  );
}
