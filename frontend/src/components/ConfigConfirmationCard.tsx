import type { SSEEvent } from "../types";

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

function valuesLine(values: Record<string, unknown> | undefined): string {
  if (!values) return "No proposed values";
  const sweepAngles = values.transducer_angles;
  if (Array.isArray(sweepAngles)) {
    const angles = sweepAngles
      .map((v) => (typeof v === "string" || typeof v === "number" ? String(v).trim() : ""))
      .filter(Boolean);
    if (angles.length > 0) return `Transducer angle sweep -> ${angles.join(", ")}`;
  }
  const pipeParts = [
    str(values.pipe_material),
    str(values.pipe_standard),
    str(values.pipe_size),
  ].filter(Boolean);
  if (pipeParts.length > 0) {
    const angle = str(values.transducer_angle);
    return [
      pipeParts.join(" / "),
      angle ? `angle ${angle}` : "",
    ].filter(Boolean).join(" / ");
  }
  if (str(values.transducer_angle)) return `Transducer angle -> ${str(values.transducer_angle)}`;
  const parts = [
    str(values.transducer_angle) ? `angle ${str(values.transducer_angle)}` : "",
  ].filter(Boolean);
  return parts.join(" / ") || JSON.stringify(values);
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
  if (values?.apply_best_after_sweep === true) {
    return "Set best measured angle at the end when a reliable score exists";
  }
  if (Array.isArray(values?.transducer_angles)) {
    return "Leave meter at the last successfully tested angle";
  }
  return "";
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
  const serial = str(workflow.serial_number);
  const current = workflow.current_values;
  const label = str(current?.label);
  const network = str(current?.network_type);
  const angles = allowedAngles(current);
  const risk = str(workflow.risk);
  const duration = durationLabel(workflow.proposed_values);
  const finalPolicy = finalPolicyLabel(workflow.proposed_values);

  return (
    <div className="my-3 max-w-2xl rounded-lg border border-amber-300/80 bg-amber-50/90 px-4 py-3 text-amber-950 shadow-sm dark:border-amber-900/70 dark:bg-amber-950/25 dark:text-amber-100">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="text-xs font-semibold uppercase tracking-normal text-amber-700 dark:text-amber-300">
            {superseded ? "Replaced" : "Confirmation required"}
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
        <p>
          <span className="font-semibold">Change:</span>{" "}
          {valuesLine(workflow.proposed_values)}
        </p>
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
            Yes, apply
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
