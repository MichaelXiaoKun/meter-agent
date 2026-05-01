import type { SSEEvent } from "../types";
import {
  configFinalPolicyLabel,
  proposedConfigLine,
} from "../configCompat";

type Workflow = NonNullable<SSEEvent["config_workflow"]>;

interface ConfigConfirmationCardProps {
  workflow: Workflow;
  disabled?: boolean;
  onConfirm?: (workflow: Workflow) => void;
  onCancel?: (workflow: Workflow) => void;
  onTypeOther?: (workflow: Workflow) => void;
}

function str(v: unknown): string {
  return typeof v === "string" && v.trim() ? v.trim() : "";
}

function allowedAngles(current: Workflow["current_values"]): string {
  const options = current?.transducer_angle_options;
  if (!Array.isArray(options)) return "";
  return options
    .map((v) => (typeof v === "string" ? v.trim() : ""))
    .filter(Boolean)
    .join(", ");
}

function ttlLabel(workflow: Workflow): string {
  const n = typeof workflow.expires_in_seconds === "number" ? workflow.expires_in_seconds : null;
  if (n == null || n <= 0) return "Expires soon";
  const min = Math.ceil(n / 60);
  return `Expires in ${min} min`;
}

function durationLabel(values: Record<string, unknown> | undefined): string {
  const raw = values?.estimated_duration_seconds;
  if (typeof raw !== "number" || !Number.isFinite(raw) || raw <= 0) return "";
  if (raw < 60) return `About ${Math.round(raw)} sec`;
  return `About ${Math.ceil(raw / 60)} min`;
}

function finalPolicyLabel(values: Record<string, unknown> | undefined): string {
  return configFinalPolicyLabel(values);
}

function field(workflow: Workflow, key: keyof Workflow): string {
  const value = workflow[key];
  return typeof value === "string" && value.trim() ? value.trim() : "";
}

function preflight(workflow: Workflow): Record<string, unknown> | null {
  const current = workflow.current_values;
  const value = current?.zero_point_preflight;
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function flowStat(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "";
  return `${Number(value.toFixed(3))} gpm`;
}

export default function ConfigConfirmationCard({
  workflow,
  disabled = false,
  onConfirm,
  onCancel,
  onTypeOther,
}: ConfigConfirmationCardProps) {
  const actionId = str(workflow.action_id);
  const status = str(workflow.status) || "pending_confirmation";
  const pending = status === "pending_confirmation";
  const superseded = status === "superseded";
  if (!pending && !superseded) return null;
  const isExperiment = workflow.workflow_type === "diagnostic_experiment";
  const isZeroPoint = workflow.tool === "set_zero_point";
  const serial = str(workflow.serial_number);
  const current = workflow.current_values;
  const label = str(current?.label);
  const network = str(current?.network_type);
  const angles = allowedAngles(current);
  const risk = str(workflow.risk);
  const duration = durationLabel(workflow.proposed_values);
  const finalPolicy = finalPolicyLabel(workflow.proposed_values);
  const zeroPreflight = preflight(workflow);
  const flowStats =
    zeroPreflight?.flow_stats && typeof zeroPreflight.flow_stats === "object"
      ? (zeroPreflight.flow_stats as Record<string, unknown>)
      : null;
  const drift =
    zeroPreflight?.drift_evidence && typeof zeroPreflight.drift_evidence === "object"
      ? (zeroPreflight.drift_evidence as Record<string, unknown>)
      : null;
  const signalPattern =
    zeroPreflight?.signal_quality_recovery_before_drift &&
    typeof zeroPreflight.signal_quality_recovery_before_drift === "object"
      ? (zeroPreflight.signal_quality_recovery_before_drift as Record<string, unknown>)
      : null;

  return (
    <div className="my-3 max-w-2xl rounded-lg border border-amber-300/80 bg-amber-50/90 px-4 py-3 text-amber-950 shadow-sm dark:border-amber-900/70 dark:bg-amber-950/25 dark:text-amber-100">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="text-xs font-semibold uppercase tracking-normal text-amber-700 dark:text-amber-300">
            {superseded
              ? "Replaced"
              : isExperiment
                ? "Diagnostic experiment"
                : isZeroPoint
                  ? "Set zero point"
                  : "Confirmation required"}
          </div>
          <div className="mt-1 text-sm font-semibold">
            {serial ? `Meter ${serial}` : "Meter configuration"}
            {label ? ` (${label})` : ""}
          </div>
        </div>
        {pending ? (
          <span className="rounded-md border border-amber-300/80 px-2 py-1 text-xs font-medium text-amber-800 dark:border-amber-800 dark:text-amber-200">
            {ttlLabel(workflow)}
          </span>
        ) : null}
      </div>

      {superseded ? (
        <p className="mt-3 text-sm leading-relaxed text-amber-900 dark:text-amber-100">
          {str(workflow.message) || "Replaced by your new request. No device change was sent."}
        </p>
      ) : null}

      {pending ? (
      <div className="mt-3 space-y-1.5 text-sm">
        {isExperiment && field(workflow, "experiment_goal") ? (
          <p>
            <span className="font-semibold">Goal:</span>{" "}
            {field(workflow, "experiment_goal")}
          </p>
        ) : null}
        {isExperiment && field(workflow, "hypothesis") ? (
          <p>
            <span className="font-semibold">Hypothesis:</span>{" "}
            {field(workflow, "hypothesis")}
          </p>
        ) : null}
        <p>
          <span className="font-semibold">{isExperiment ? "Experiment:" : isZeroPoint ? "Command:" : "Change:"}</span>{" "}
          {proposedConfigLine(workflow.proposed_values)}
        </p>
        {isZeroPoint && field(workflow, "preflight_summary") ? (
          <p>
            <span className="font-semibold">Preflight:</span>{" "}
            {field(workflow, "preflight_summary")}
          </p>
        ) : null}
        {isZeroPoint && field(workflow, "flow_state") ? (
          <p>
            <span className="font-semibold">Flow gate:</span>{" "}
            {field(workflow, "flow_state").replace(/_/g, " ")}
          </p>
        ) : null}
        {isZeroPoint && flowStats ? (
          <p>
            <span className="font-semibold">Recent flow:</span>{" "}
            {[
              flowStat(flowStats.latest_flow_gpm) && `latest ${flowStat(flowStats.latest_flow_gpm)}`,
              flowStat(flowStats.recent_p90_abs_gpm) && `p90 |flow| ${flowStat(flowStats.recent_p90_abs_gpm)}`,
              typeof flowStats.recent_row_count === "number" ? `${flowStats.recent_row_count} samples` : "",
            ].filter(Boolean).join(", ")}
          </p>
        ) : null}
        {isZeroPoint && drift ? (
          <p>
            <span className="font-semibold">Drift check:</span>{" "}
            {drift.detected ? "Drift evidence present" : "Drift evidence inconclusive"}
            {str(drift.direction) ? ` (${str(drift.direction)})` : ""}
          </p>
        ) : null}
        {isZeroPoint && signalPattern ? (
          <p>
            <span className="font-semibold">Signal pattern:</span>{" "}
            {signalPattern.detected
              ? "High-low-high recovery before estimated drift"
              : "No confirmed high-low-high recovery pattern"}
          </p>
        ) : null}
        {isExperiment && field(workflow, "measurement_plan") ? (
          <p>
            <span className="font-semibold">Measurement:</span>{" "}
            {field(workflow, "measurement_plan")}
          </p>
        ) : null}
        {isExperiment && field(workflow, "success_criteria") ? (
          <p>
            <span className="font-semibold">Success criteria:</span>{" "}
            {field(workflow, "success_criteria")}
          </p>
        ) : null}
        {network ? (
          <p>
            <span className="font-semibold">Network:</span> {network}
          </p>
        ) : null}
        {angles ? (
          <p>
            <span className="font-semibold">Allowed angles:</span> {angles}
          </p>
        ) : null}
        {duration ? (
          <p>
            <span className="font-semibold">Estimated runtime:</span> {duration}
          </p>
        ) : null}
        {finalPolicy ? (
          <p>
            <span className="font-semibold">Final angle:</span> {finalPolicy}
          </p>
        ) : null}
        {isExperiment && field(workflow, "final_policy") ? (
          <p>
            <span className="font-semibold">Final policy:</span>{" "}
            {field(workflow, "final_policy")}
          </p>
        ) : null}
        {risk ? (
          <p className="text-xs leading-relaxed text-amber-800 dark:text-amber-200">
            {risk}
          </p>
        ) : null}
      </div>
      ) : null}

      {pending && actionId ? (
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            disabled={disabled}
            onClick={() => onConfirm?.(workflow)}
            className="min-h-9 rounded-md bg-amber-700 px-3 py-2 text-sm font-semibold text-white transition hover:bg-amber-800 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isExperiment ? "Run experiment" : isZeroPoint ? "Set zero point" : "Yes, apply"}
          </button>
          <button
            type="button"
            disabled={disabled}
            onClick={() => onCancel?.(workflow)}
            className="min-h-9 rounded-md border border-amber-300 bg-white/75 px-3 py-2 text-sm font-semibold text-amber-900 transition hover:bg-white disabled:cursor-not-allowed disabled:opacity-50 dark:border-amber-800 dark:bg-white/[0.06] dark:text-amber-100 dark:hover:bg-white/[0.1]"
          >
            No, cancel
          </button>
          <button
            type="button"
            disabled={disabled}
            onClick={() => onTypeOther?.(workflow)}
            className="min-h-9 rounded-md border border-amber-300 bg-transparent px-3 py-2 text-sm font-semibold text-amber-900 transition hover:bg-amber-100/70 disabled:cursor-not-allowed disabled:opacity-50 dark:border-amber-800 dark:text-amber-100 dark:hover:bg-white/[0.08]"
          >
            Type other
          </button>
        </div>
      ) : null}
    </div>
  );
}
