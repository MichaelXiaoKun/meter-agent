/**
 * Skeleton loader for messages during loading state.
 * More visually appealing than spinners for message content.
 */
export function MessageSkeleton() {
  return (
    <div className="flex flex-col gap-2 items-start">
      <div className="max-w-[min(92%,28rem)] min-w-0 overflow-hidden rounded-2xl border border-brand-border bg-white px-4 py-3.5 text-brand-900 dark:border-brand-border dark:bg-brand-50 sm:max-w-[75%] sm:py-3 space-y-3">
        {/* Skeleton lines */}
        <div className="space-y-2">
          <div className="h-4 bg-brand-200/40 dark:bg-brand-800/30 rounded animate-pulse" style={{ width: "85%" }} />
          <div className="h-4 bg-brand-200/40 dark:bg-brand-800/30 rounded animate-pulse" style={{ width: "92%", animationDelay: "0.15s" }} />
          <div className="h-4 bg-brand-200/40 dark:bg-brand-800/30 rounded animate-pulse" style={{ width: "78%", animationDelay: "0.3s" }} />
        </div>
      </div>
    </div>
  );
}

export function MessageSkeletonGroup({ count = 3 }: { count?: number }) {
  return (
    <div className="space-y-3">
      {Array.from({ length: count }).map((_, i) => (
        <MessageSkeleton key={i} />
      ))}
    </div>
  );
}
