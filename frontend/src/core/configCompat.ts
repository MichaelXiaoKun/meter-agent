import type { SSEEvent } from "./types";

export type ConfigWorkflow = NonNullable<SSEEvent["config_workflow"]>;

export function cleanConfigString(value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "string") return value.trim();
  return "";
}

function compactJson(value: unknown): string {
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function prettyKey(value: unknown): string {
  const s = cleanConfigString(value);
  if (!s) return "";
  return s
    .replace(/^set_/u, "")
    .replace(/_/gu, " ")
    .replace(/^\w/u, (m) => m.toUpperCase());
}

export function configSerial(workflow: ConfigWorkflow): string {
  return (
    cleanConfigString(workflow.serial_number) ||
    cleanConfigString(workflow.proposed_values?.serial_number)
  );
}

export function configAngle(values: Record<string, unknown> | undefined): string {
  return (
    cleanConfigString(values?.transducer_angle) ||
    cleanConfigString(values?.angle_degrees) ||
    (cleanConfigString(values?.action).toLowerCase().includes("angle")
      ? cleanConfigString(values?.value)
      : "")
  );
}

export function angleLabel(value: unknown): string {
  const raw = cleanConfigString(value);
  if (!raw) return "";
  return raw.endsWith("°") || raw.endsWith("º") ? raw : `${raw}°`;
}

export function configAngleLabel(values: Record<string, unknown> | undefined): string {
  return angleLabel(configAngle(values));
}

export function configSweepAngles(values: Record<string, unknown> | undefined): string[] {
  const raw = Array.isArray(values?.transducer_angles)
    ? values?.transducer_angles
    : Array.isArray(values?.angles)
      ? values?.angles
      : [];
  return raw
    .map((v) => cleanConfigString(v))
    .filter(Boolean);
}

export function configSweepRangeLabel(values: Record<string, unknown> | undefined): string {
  const min = cleanConfigString(values?.min_angle);
  const max = cleanConfigString(values?.max_angle);
  const step = cleanConfigString(values?.step);
  if (!min && !max) return "";
  return [
    min && max ? `${angleLabel(min)} to ${angleLabel(max)}` : angleLabel(min || max),
    step ? `step ${angleLabel(step)}` : "",
  ].filter(Boolean).join(", ");
}

export function configFinalPolicyLabel(values: Record<string, unknown> | undefined): string {
  if (values?.apply_best_after_sweep === true || values?.apply_best === true) {
    return "Set best measured angle at the end when a reliable score exists";
  }
  if (configSweepAngles(values).length > 0 || configSweepRangeLabel(values)) {
    return "Leave meter at the last successfully tested angle";
  }
  return "";
}

export function proposedConfigLine(values: Record<string, unknown> | undefined): string {
  if (!values) return "No proposed values";

  if (
    cleanConfigString(values.action) === "set_zero_point" ||
    (values.mqtt_payload &&
      typeof values.mqtt_payload === "object" &&
      cleanConfigString((values.mqtt_payload as Record<string, unknown>).szv) === "null")
  ) {
    return 'Set zero point -> {"szv":"null"}';
  }

  const sweepAngles = configSweepAngles(values);
  if (sweepAngles.length > 0) {
    return `Transducer angle sweep -> ${sweepAngles.map(angleLabel).join(", ")}`;
  }

  const sweepRange = configSweepRangeLabel(values);
  if (sweepRange) return `Transducer angle sweep -> ${sweepRange}`;

  const pipeParts = [
    cleanConfigString(values.pipe_material),
    cleanConfigString(values.pipe_standard),
    cleanConfigString(values.pipe_size),
  ].filter(Boolean);
  if (pipeParts.length > 0) {
    const angle = configAngleLabel(values);
    return [...pipeParts, angle ? `angle ${angle}` : ""].filter(Boolean).join(" / ");
  }

  const action = prettyKey(values.action);
  const rawValue = values.value;
  const value =
    rawValue != null && typeof rawValue === "object"
      ? compactJson(rawValue)
      : cleanConfigString(rawValue);
  if (action && value) return `${action} -> ${value}`;
  if (action) return action;

  const angle = configAngleLabel(values);
  if (angle) return `Transducer angle -> ${angle}`;

  return compactJson(values);
}
