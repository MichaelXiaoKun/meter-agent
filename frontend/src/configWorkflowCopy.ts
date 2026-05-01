import type { SSEEvent } from "./types";
import {
  angleLabel,
  configAngle,
  configSerial,
  configSweepAngles,
} from "./configCompat";

export type ConfigWorkflow = NonNullable<SSEEvent["config_workflow"]>;

export function confirmationUserMessage(workflow: ConfigWorkflow): string {
  const serial = configSerial(workflow);
  const meterLabel = serial ? `meter ${serial}` : "this meter";
  if (workflow.tool === "sweep_transducer_angles") {
    const count = configSweepAngles(workflow.proposed_values).length;
    if (workflow.workflow_type === "diagnostic_experiment") {
      return `Yes, run the ${count ? `${count}-angle ` : ""}diagnostic angle sweep for ${meterLabel} and set the best reliable angle if available.`;
    }
    const suffix =
      workflow.proposed_values?.apply_best_after_sweep === true ||
      workflow.proposed_values?.apply_best === true
        ? " and set the best measured angle if available"
        : "";
    return `Yes, run the ${count ? `${count}-angle ` : ""}transducer angle sweep for ${meterLabel}${suffix}.`;
  }
  if (workflow.tool === "configure_meter_pipe") {
    return `Yes, apply the pipe configuration for ${meterLabel}.`;
  }
  if (workflow.tool === "set_zero_point") {
    return `Yes, confirm there is no intended water flow and put ${meterLabel} into set-zero-point state.`;
  }
  const angle = angleLabel(configAngle(workflow.proposed_values));
  if (angle) return `Yes, set ${meterLabel} to ${angle}.`;
  return `Yes, apply the pipe configuration for ${meterLabel}.`;
}

export function cancellationUserMessage(): string {
  return "No, cancel this configuration change.";
}
