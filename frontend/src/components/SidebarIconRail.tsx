/**
 * Narrow icon column when the full conversation sidebar is collapsed.
 */

function IconChevronRight({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      fill="none"
      stroke="currentColor"
      viewBox="0 0 24 24"
      aria-hidden
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M9 5l7 7-7 7"
      />
    </svg>
  );
}

/** Pencil with writing lines — “compose / new message”. */
function IconPencilWriting({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      fill="none"
      stroke="currentColor"
      viewBox="0 0 24 24"
      aria-hidden
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L7.5 20.5 3 21l.5-4.5L16.732 3.732z"
      />
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M4 20h4M4 16h7M4 12h5"
        opacity={0.45}
      />
    </svg>
  );
}

interface SidebarIconRailProps {
  onExpand: () => void;
  onNewConversation: () => void;
}

export default function SidebarIconRail({
  onExpand,
  onNewConversation,
}: SidebarIconRailProps) {
  return (
    <aside className="flex h-full w-14 min-w-[3.5rem] flex-col items-center gap-2 bg-brand-100 py-3">
      <button
        type="button"
        onClick={onExpand}
        className="group relative flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-xl border border-brand-border bg-white text-brand-700 shadow-sm ring-1 ring-brand-border/50 transition-[border-color,box-shadow,background-color] hover:border-brand-400 hover:bg-brand-50"
        title="Expand sidebar"
        aria-label="Expand conversations sidebar"
      >
        <img
          src="/api/logo"
          alt=""
          width={32}
          height={32}
          className="h-8 w-8 rounded-md object-cover transition-opacity duration-200 ease-out group-hover:opacity-0 group-focus-visible:opacity-0"
        />
        <span
          className="pointer-events-none absolute inset-0 flex items-center justify-center opacity-0 transition-opacity duration-200 ease-out group-hover:opacity-100 group-focus-visible:opacity-100"
          aria-hidden
        >
          <IconChevronRight className="h-5 w-5" />
        </span>
      </button>

      <div className="h-px w-8 bg-brand-border/80" aria-hidden />

      <button
        type="button"
        onClick={onNewConversation}
        className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-brand-border bg-white text-brand-700 shadow-sm ring-1 ring-brand-border/50 transition-colors hover:border-brand-500 hover:bg-brand-50 hover:text-brand-900"
        title="New conversation"
        aria-label="New conversation"
      >
        <IconPencilWriting className="h-5 w-5" />
      </button>
    </aside>
  );
}
