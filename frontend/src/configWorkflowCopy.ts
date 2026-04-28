import type { SSEEvent } from "./types";

export type ConfigWorkflow = NonNullable<SSEEvent["config_workflow"]>;

function cleanString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function angleLabel(value: unknown): string {
  const raw =
    typeof value === "number"
      ? String(value)
      : typeof value === "string"
        ? value.trim()
        : "";
  if (!raw) return "";
  return raw.endsWith("°") ? raw : `${raw}°`;
}

export function confirmationUserMessage(workflow: ConfigWorkflow): string {
  const serial =
    cleanString(workflow.serial_number) ||
    cleanString(workflow.proposed_values?.serial_number);
  const meterLabel = serial ? `meter ${serial}` : "this meter";
  if (workflow.tool === "sweep_transducer_angles") {
    const angles = workflow.proposed_values?.transducer_angles;
    const count = Array.isArray(angles) ? angles.length : 0;
    const suffix =
      workflow.proposed_values?.apply_best_after_sweep === true
        ? " and set the best measured angle if available"
        : "";
    return `Yes, run the ${count ? `${count}-angle ` : ""}transducer angle sweep for ${meterLabel}${suffix}.`;
  }
  if (workflow.tool === "configure_meter_pipe") {
    return `Yes, apply the pipe configuration for ${meterLabel}.`;
  }
  const angle = angleLabel(workflow.proposed_values?.transducer_angle);
  if (angle) return `Yes, set ${meterLabel} to ${angle}.`;
  return `Yes, apply the pipe configuration for ${meterLabel}.`;
}

export function cancellationUserMessage(): string {
  return "No, cancel this configuration change.";
}
