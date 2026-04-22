import type { OrchestratorModelOption } from "../api";

const STORAGE_KEY = "bb_orchestrator_model";

/** Read the stored model ID, validating against the current allowlist. */
export function readStoredModel(
  models: OrchestratorModelOption[] | undefined,
  defaultModel: string | undefined,
): string | null {
  if (!models || models.length === 0) return defaultModel ?? null;
  const ids = new Set(models.map((m) => m.id));
  try {
    const raw =
      typeof localStorage !== "undefined"
        ? localStorage.getItem(STORAGE_KEY)
        : null;
    if (raw && ids.has(raw)) return raw;
  } catch {
    /* localStorage unavailable — fall through to default */
  }
  if (defaultModel && ids.has(defaultModel)) return defaultModel;
  return models[0]?.id ?? null;
}

/** Persist a picked model; no-op if storage is unavailable. */
export function writeStoredModel(modelId: string): void {
  try {
    if (typeof localStorage !== "undefined") {
      localStorage.setItem(STORAGE_KEY, modelId);
    }
  } catch {
    /* ignore quota / private mode */
  }
}
