/**
 * Collapsed layout: logo (opens full sidebar) and new-conversation — no rail chrome.
 */

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
    <nav
      className="flex h-full min-h-0 w-full flex-col items-center gap-3 bg-transparent px-0 pt-[max(0.75rem,env(safe-area-inset-top,0px))] pb-[max(0.75rem,env(safe-area-inset-bottom,0px))]"
      aria-label="Conversations"
    >
      <button
        type="button"
        onClick={onExpand}
        className="flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-xl border border-brand-border/80 bg-white text-brand-700 shadow-sm ring-1 ring-brand-border/40 transition-[border-color,box-shadow,background-color] hover:border-brand-400 hover:bg-brand-50"
        title="Open conversations"
        aria-label="Open conversations sidebar"
      >
        <img
          src="/api/logo"
          alt=""
          width={32}
          height={32}
          className="h-8 w-8 rounded-md object-cover"
        />
      </button>

      <button
        type="button"
        onClick={onNewConversation}
        className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-brand-border/80 bg-white text-brand-700 shadow-sm ring-1 ring-brand-border/40 transition-colors hover:border-brand-500 hover:bg-brand-50 hover:text-brand-900"
        title="New conversation"
        aria-label="New conversation"
      >
        <IconPencilWriting className="h-5 w-5" />
      </button>
    </nav>
  );
}
