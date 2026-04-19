import type { AgentStatus } from "../hooks/useChat";

const TOOL_LABELS: Record<string, string> = {
  resolve_time_range: "Resolving time range",
  check_meter_status: "Checking meter status",
  get_meter_profile: "Fetching meter profile",
  analyze_flow_data: "Analyzing flow data",
  configure_meter_pipe: "Configuring meter pipe",
  set_transducer_angle_only: "Setting transducer angle (SSA only)",
};

/** Short label after a tool finishes (before the next model turn streams). */
const TOOL_RESULT_DONE: Record<string, string> = {
  resolve_time_range: "Time range ready",
  check_meter_status: "Meter status received",
  get_meter_profile: "Meter profile received",
  analyze_flow_data: "Flow analysis complete",
  configure_meter_pipe: "Pipe configuration updated",
  set_transducer_angle_only: "Angle update sent",
};

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
    case "thinking":
      label = "Preparing reply...";
      break;
    case "streaming":
      label = "Generating reply...";
      break;
    case "tool_call":
      label = `${TOOL_LABELS[status.tool] ?? status.tool}...`;
      break;
    case "tool_progress":
      label = status.message;
      break;
    case "tool_result":
      if (status.success) {
        label =
          TOOL_RESULT_DONE[status.tool] ??
          `${status.tool.replace(/_/g, " ")} complete`;
      } else {
        label = `${TOOL_LABELS[status.tool] ?? status.tool} failed`;
        variant = "error";
      }
      break;
    case "compressing":
      label = "Compressing conversation history...";
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
