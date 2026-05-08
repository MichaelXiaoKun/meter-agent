import type { SSEEvent } from "../../../core/types";
import { angleLabel } from "../../../core/configCompat";

type SweepResult = NonNullable<SSEEvent["sweep_result"]>;

function scoreLabel(value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value)) return String(Math.round(value * 10) / 10);
  if (typeof value === "string" && value.trim()) return value.trim();
  return "n/a";
}

function str(value: unknown): string {
  return typeof value === "string" && value.trim() ? value.trim() : "";
}

function reliableLabel(value: unknown): string {
  if (value === true) return "yes";
  if (value === false) return "no";
  return "unknown";
}

export default function SweepResultCard({ result }: { result: SweepResult }) {
  const ranking = Array.isArray(result.ranking) ? result.ranking : [];
  const rows = ranking.length
    ? ranking
    : (Array.isArray(result.results) ? result.results : []).map((row) => {
        const signal = row.signal && typeof row.signal === "object" ? row.signal : {};
        return {
          angle: row.angle,
          signal_score: signal.score as number | string | null | undefined,
          signal_level: signal.level as string | null | undefined,
          reliable: signal.reliable as boolean | null | undefined,
        };
      });
  if (!rows.length && !result.final_action && !result.notice) return null;

  const best = str(result.best_angle);
  const finalAngle = str(result.final_angle);
  const finalAction = str(result.final_action).replace(/_/gu, " ");
  const noReliableBest = result.final_action === "best_not_set_no_reliable_score" || !best;

  return (
    <div className="mt-3 max-w-2xl rounded-lg border border-brand-border bg-brand-50/80 px-4 py-3 text-sm text-brand-text shadow-sm dark:bg-white/[0.04]">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="text-xs font-semibold uppercase tracking-normal text-brand-muted">
            Angle sweep result
          </div>
          <div className="mt-1 font-semibold">
            {best ? `Best measured angle: ${angleLabel(best)}` : "No reliable best angle"}
          </div>
        </div>
        {finalAngle ? (
          <span className="rounded-md border border-brand-border px-2 py-1 text-xs font-medium text-brand-muted">
            Final {angleLabel(finalAngle)}
          </span>
        ) : null}
      </div>

      <div className="mt-3 overflow-x-auto">
        <table className="w-full min-w-[420px] border-collapse text-left text-xs">
          <thead className="text-brand-muted">
            <tr>
              <th className="border-b border-brand-border py-2 pr-3 font-semibold">Angle</th>
              <th className="border-b border-brand-border py-2 pr-3 font-semibold">Score</th>
              <th className="border-b border-brand-border py-2 pr-3 font-semibold">Level</th>
              <th className="border-b border-brand-border py-2 font-semibold">Reliable</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={`${row.angle ?? "angle"}-${index}`}>
                <td className="border-b border-brand-border/70 py-2 pr-3 font-medium">
                  {angleLabel(row.angle)}
                </td>
                <td className="border-b border-brand-border/70 py-2 pr-3">
                  {scoreLabel(row.signal_score)}
                </td>
                <td className="border-b border-brand-border/70 py-2 pr-3">
                  {str(row.signal_level) || "n/a"}
                </td>
                <td className="border-b border-brand-border/70 py-2">
                  {reliableLabel(row.reliable)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {finalAction ? (
        <p className="mt-3 text-xs text-brand-muted">Final action: {finalAction}</p>
      ) : null}
      {noReliableBest ? (
        <p className="mt-2 text-xs font-medium text-amber-700 dark:text-amber-300">
          No reliable best score was available, so this should not be treated as a confirmed optimization.
        </p>
      ) : null}
      {str(result.notice) ? (
        <p className="mt-2 text-xs text-brand-muted">{str(result.notice)}</p>
      ) : null}
    </div>
  );
}
