import type { AgentStatus } from "../hooks/useChat";
import { toolDoneLine, toolNowLine } from "../turnActivity";

interface StatusIndicatorProps {
  status: AgentStatus;
}

export default function StatusIndicator({ status }: StatusIndicatorProps) {
  if (status.kind === "idle") return null;
  // Errors use the dismissible banner in ChatView (clearer than a line at the bottom).
  if (status.kind === "error") return null;

  let label: string;
  let variant: "info" | "error" = "info";

  switch (status.kind) {
    case "queued":
      label = status.message;
      break;
    case "connecting":
      label = "Sending your message…";
      break;
    case "thinking":
      label = "Thinking…";
      break;
    case "streaming":
      label = "Writing the reply…";
      break;
    case "tool_call":
      label = toolNowLine(status.tool);
      break;
    case "tool_progress":
      label = status.message;
      break;
    case "tool_result":
      label = toolDoneLine(status.tool, status.success);
      if (!status.success) {
        variant = "error";
      }
      break;
    case "compressing":
      label = "Tightening context…";
      break;
    default:
      return null;
  }

  return (
    <div
      className={`flex items-center gap-2 pl-0.5 text-left text-sm ${
        variant === "error"
          ? "text-red-600/90 dark:text-red-400/90"
          : "text-neutral-500 dark:text-neutral-400"
      }`}
    >
      {variant === "info" && (
        <svg
          className="h-3.5 w-3.5 shrink-0 animate-spin text-neutral-400 dark:text-neutral-500"
          fill="none"
          viewBox="0 0 24 24"
          aria-hidden
        >
          <circle
            className="opacity-25"
            cx="12"
            cy="12"
            r="10"
            stroke="currentColor"
            strokeWidth="4"
          />
          <path
            className="opacity-75"
            fill="currentColor"
            d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
          />
        </svg>
      )}
      <span className="min-w-0 leading-relaxed">{label}</span>
    </div>
  );
}
