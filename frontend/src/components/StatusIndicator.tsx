import type { AgentStatus } from "../hooks/useChat";

const TOOL_LABELS: Record<string, string> = {
  resolve_time_range: "Resolving time range",
  check_meter_status: "Checking meter status",
  analyze_flow_data: "Analysing flow data",
  configure_meter_pipe: "Configuring meter pipe",
  set_transducer_angle_only: "Setting transducer angle (SSA only)",
};

interface StatusIndicatorProps {
  status: AgentStatus;
}

export default function StatusIndicator({ status }: StatusIndicatorProps) {
  if (status.kind === "idle") return null;

  let label: string;
  let variant: "info" | "error" = "info";

  switch (status.kind) {
    case "thinking":
      label = "Thinking...";
      break;
    case "streaming":
      return null;
    case "tool_call":
      label = `${TOOL_LABELS[status.tool] ?? status.tool}...`;
      break;
    case "tool_result":
      label = `${status.tool} ${status.success ? "done" : "failed"}`;
      if (!status.success) variant = "error";
      break;
    case "compressing":
      label = "Compressing conversation history...";
      break;
    case "error":
      label = status.error;
      variant = "error";
      break;
    default:
      return null;
  }

  return (
    <div
      className={`flex items-center gap-2 rounded-lg px-3 py-2 text-sm ${
        variant === "error"
          ? "bg-red-50 text-red-700"
          : "bg-brand-100 text-brand-muted"
      }`}
    >
      {variant === "info" && (
        <svg
          className="h-4 w-4 animate-spin text-brand-500"
          fill="none"
          viewBox="0 0 24 24"
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
      {label}
    </div>
  );
}
