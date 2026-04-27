import { useState } from "react";
import type { ReactNode } from "react";
import type { MeterWorkspaceState } from "../meterWorkspace";
import {
  attributionLabel,
  confidenceLabel,
  driftLabel,
  severityLabel,
  signalLabel,
} from "../meterWorkspace";
import PlotImage from "./PlotImage";

interface MeterWorkspacePanelProps {
  workspace: MeterWorkspaceState;
  processing: boolean;
  onCompose: (message: string) => void;
  onConfirmConfig: (actionId: string) => void;
}

function Field({ label, value }: { label: string; value?: string | number | null }) {
  if (value == null || value === "") return null;
  return (
    <div className="min-w-0">
      <div className="text-[10px] font-semibold uppercase tracking-normal text-brand-muted/75">
        {label}
      </div>
      <div className="truncate text-sm text-brand-900">{value}</div>
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="border-b border-brand-border/70 px-4 py-4 last:border-b-0">
      <h2 className="mb-3 text-xs font-semibold uppercase tracking-normal text-brand-muted">
        {title}
      </h2>
      {children}
    </section>
  );
}

function Explain({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <details className="group rounded-md border border-brand-border/75 bg-white/60 px-3 py-2 text-xs text-brand-muted dark:bg-white/[0.04]">
      <summary className="cursor-pointer list-none font-medium text-brand-800 marker:hidden dark:text-brand-900">
        {title}
      </summary>
      <div className="mt-2 leading-relaxed">{children}</div>
    </details>
  );
}

function formatPipe(pipe: Record<string, unknown> | null | undefined): string {
  if (!pipe) return "Unknown";
  const material = typeof pipe.material === "string" ? pipe.material : "";
  const standard = typeof pipe.standard === "string" ? pipe.standard : "";
  const size =
    typeof pipe.nominal_size === "string"
      ? pipe.nominal_size
      : typeof pipe.pipe_size === "string"
        ? pipe.pipe_size
        : "";
  const inner =
    typeof pipe.inner_diameter_mm === "number"
      ? `${Math.round(pipe.inner_diameter_mm * 10) / 10} mm ID`
      : "";
  return [material, standard, size, inner].filter(Boolean).join(" / ") || "Unknown";
}

function proposedLine(values: Record<string, unknown> | undefined): string {
  if (!values) return "No proposed values";
  const parts = [
    values.pipe_material,
    values.pipe_standard,
    values.pipe_size,
    values.transducer_angle ? `angle ${values.transducer_angle}` : "",
  ]
    .filter((v): v is string => typeof v === "string" && v.trim().length > 0)
    .map((v) => v.trim());
  return parts.join(" / ") || JSON.stringify(values);
}

function textValue(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function listValues(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is string => typeof item === "string" && item.trim().length > 0)
    .map((item) => item.trim());
}

function evidenceMessages(
  attribution: Record<string, unknown> | null | undefined,
  keywords: string[],
): string[] {
  const raw = attribution?.evidence;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((item): item is Record<string, unknown> => item != null && typeof item === "object")
    .filter((item) => {
      const haystack = `${item.code ?? ""} ${item.source ?? ""} ${item.message ?? ""}`.toLowerCase();
      return keywords.some((kw) => haystack.includes(kw));
    })
    .map((item) => textValue(item.message) ?? textValue(item.code) ?? "")
    .filter(Boolean)
    .slice(0, 3);
}

function AttributionCard({ attribution }: { attribution: Record<string, unknown> }) {
  const summary = textValue(attribution.summary);
  const cause = textValue(attribution.primary_cause);
  const nextChecks = listValues(attribution.next_checks).slice(0, 3);
  const evidence = evidenceMessages(attribution, [""]).slice(0, 3);
  return (
    <div className="space-y-2 rounded-md border border-brand-border/80 bg-white/75 px-3 py-3 text-xs dark:bg-white/[0.04]">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-brand-900">
          {attributionLabel(attribution)}
        </span>
        <span className="rounded border border-brand-border px-2 py-0.5 font-medium text-brand-muted">
          Severity {severityLabel(attribution)}
        </span>
        <span className="rounded border border-brand-border px-2 py-0.5 font-medium text-brand-muted">
          Confidence {confidenceLabel(attribution)}
        </span>
      </div>
      {summary && <p className="leading-relaxed text-brand-muted">{summary}</p>}
      {cause && <p className="leading-relaxed text-brand-800">{cause}</p>}
      {evidence.length > 0 && (
        <ul className="space-y-1 text-brand-muted">
          {evidence.map((item) => (
            <li key={item}>Evidence: {item}</li>
          ))}
        </ul>
      )}
      {nextChecks.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {nextChecks.map((check) => (
            <span
              key={check}
              className="rounded-md bg-brand-100 px-2 py-1 font-medium text-brand-800 dark:bg-white/[0.08] dark:text-brand-900"
            >
              {check}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

const ANALYSIS_TABS = [
  { id: "overview", label: "Overview" },
  { id: "flow", label: "Flow" },
  { id: "quality", label: "Quality" },
  { id: "drift", label: "Drift" },
  { id: "gaps", label: "Gaps" },
] as const;

type AnalysisTab = (typeof ANALYSIS_TABS)[number]["id"];

function plotForTab(flow: MeterWorkspaceState["flow"], tab: AnalysisTab) {
  const plots = flow?.plots ?? [];
  const wanted: Record<AnalysisTab, string[]> = {
    overview: ["time_series", "peaks_annotated"],
    flow: ["time_series", "flow_duration_curve"],
    quality: ["signal_quality"],
    drift: ["time_series", "peaks_annotated"],
    gaps: ["time_series"],
  };
  return plots.find((p) => p.plotType && wanted[tab].includes(p.plotType)) ?? plots[0];
}

function TabSummary({
  workspace,
  tab,
}: {
  workspace: MeterWorkspaceState;
  tab: AnalysisTab;
}) {
  const flow = workspace.flow;
  if (!flow) return null;
  if (tab === "overview") {
    return (
      <p className="text-xs leading-relaxed text-brand-muted">
        {textValue(flow.attribution?.summary) ||
          flow.adequacyExplanation ||
          "Latest flow analysis is ready."}
      </p>
    );
  }
  if (tab === "flow") {
    return (
      <p className="text-xs leading-relaxed text-brand-muted">
        Use the flow chart to inspect timing, volume changes, and demand peaks in the selected window.
      </p>
    );
  }
  if (tab === "quality") {
    const evidence = evidenceMessages(flow.attribution, ["quality", "flatline", "signal"]);
    if (evidence.length > 0) {
      return (
        <div className="space-y-1 text-xs leading-relaxed text-brand-muted">
          {evidence.map((item) => (
            <p key={item}>{item}</p>
          ))}
        </div>
      );
    }
    return (
      <p className="text-xs leading-relaxed text-brand-muted">
        Signal quality helps separate real hydraulic changes from weak readings or telemetry issues.
      </p>
    );
  }
  if (tab === "drift") {
    const evidence = evidenceMessages(flow.attribution, ["cusum", "drift", "adequacy"]);
    if (evidence.length > 0) {
      return (
        <div className="space-y-1 text-xs leading-relaxed text-brand-muted">
          {evidence.map((item) => (
            <p key={item}>{item}</p>
          ))}
        </div>
      );
    }
    return (
      <p className="text-xs leading-relaxed text-brand-muted">
        CUSUM result: {driftLabel(flow)}. Sustained drift means the cumulative change kept moving in one direction.
      </p>
    );
  }
  {
    const evidence = evidenceMessages(flow.attribution, ["gap", "coverage", "sampling"]);
    if (evidence.length > 0) {
      return (
        <div className="space-y-1 text-xs leading-relaxed text-brand-muted">
          {evidence.map((item) => (
            <p key={item}>{item}</p>
          ))}
        </div>
      );
    }
  }
  return (
    <p className="text-xs leading-relaxed text-brand-muted">
      Gap checks show whether missing samples make the analysis less reliable. If adequacy is skipped, widen the window.
    </p>
  );
}

export default function MeterWorkspacePanel({
  workspace,
  processing,
  onCompose,
  onConfirmConfig,
}: MeterWorkspacePanelProps) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const [analysisTab, setAnalysisTab] = useState<AnalysisTab>("overview");
  const hasMeter = Boolean(workspace.serialNumber);
  const pending = workspace.pendingConfig;
  const serial = workspace.serialNumber ?? pending?.serial_number;
  const selectedPlot = plotForTab(workspace.flow, analysisTab);

  const body = (
    <div className="min-h-0 overflow-y-auto">
      <Section title="Meter">
        {serial ? (
          <div className="space-y-3">
            <div>
              <div className="text-lg font-semibold leading-tight text-brand-900">
                {workspace.label || serial}
              </div>
              {workspace.label && (
                <div className="text-xs font-mono text-brand-muted">{serial}</div>
              )}
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Network" value={workspace.networkType ?? "Unknown"} />
              <Field label="Timezone" value={workspace.timezone ?? "Unknown"} />
              <Field
                label="Online"
                value={
                  workspace.online === true
                    ? "Online"
                    : workspace.online === false
                      ? "Offline"
                      : "Unknown"
                }
              />
              <Field label="Signal" value={signalLabel(workspace.signal)} />
            </div>
          </div>
        ) : (
          <p className="text-sm leading-relaxed text-brand-muted">
            Ask about a meter serial to build a workspace here.
          </p>
        )}
      </Section>

      <Section title="Flow Analysis">
        {workspace.flow ? (
          <div className="space-y-3">
            <Field label="Window" value={workspace.flow.range ?? "Latest analysis"} />
            {workspace.flow.attribution && (
              <AttributionCard attribution={workspace.flow.attribution} />
            )}
            <div className="grid grid-cols-2 gap-3">
              <Field label="CUSUM" value={workspace.flow.drift?.skipped ? "Skipped" : "Checked"} />
              <Field label="Drift" value={driftLabel(workspace.flow)} />
              <Field
                label="Alarms"
                value={
                  workspace.flow.alarms
                    ? `${workspace.flow.alarms.up ?? 0} up / ${workspace.flow.alarms.down ?? 0} down`
                    : "None"
                }
              />
              <Field label="Plots" value={workspace.flow.plotCount ?? 0} />
            </div>
            {workspace.flow.adequacyExplanation && (
              <p className="rounded-md border border-emerald-200/75 bg-emerald-50/80 px-3 py-2 text-xs leading-relaxed text-emerald-800 dark:border-emerald-900/70 dark:bg-emerald-950/25 dark:text-emerald-200">
                {workspace.flow.adequacyExplanation}
              </p>
            )}
            <Explain title="How CUSUM decides drift">
              CUSUM looks for sustained upward or downward change, not one noisy spike.
              It only runs when the data adequacy check passes, so sparse windows do not
              create false confidence.
            </Explain>
            <Explain title="What adequacy means">
              Adequacy checks whether the window has enough samples and acceptable gaps
              before drift detection. If it fails, the workspace recommends widening the
              time window or checking connectivity.
            </Explain>
            <div className="space-y-3 rounded-lg border border-brand-border/80 bg-white/65 p-2.5 dark:bg-white/[0.04]">
              <div className="grid grid-cols-5 gap-1" role="tablist" aria-label="Flow analysis views">
                {ANALYSIS_TABS.map((tab) => (
                  <button
                    key={tab.id}
                    type="button"
                    role="tab"
                    aria-selected={analysisTab === tab.id}
                    onClick={() => setAnalysisTab(tab.id)}
                    className={[
                      "min-h-8 rounded-md px-1.5 text-[10px] font-semibold transition",
                      analysisTab === tab.id
                        ? "bg-brand-700 text-white shadow-sm"
                        : "bg-transparent text-brand-muted hover:bg-brand-100 hover:text-brand-900 dark:hover:bg-white/[0.08]",
                    ].join(" ")}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>
              <TabSummary workspace={workspace} tab={analysisTab} />
              {selectedPlot ? (
                <PlotImage
                  src={selectedPlot.src}
                  alt="Flow analysis plot"
                  title={selectedPlot.title}
                  plotTimezone={selectedPlot.plotTimezone}
                  plotType={selectedPlot.plotType}
                  caption={selectedPlot.caption}
                  className="rounded-md border border-brand-border/70 bg-white p-1 dark:bg-brand-100/50"
                />
              ) : (
                <div className="rounded-md border border-dashed border-brand-border px-3 py-6 text-center text-xs text-brand-muted">
                  Plots will appear here after the next flow analysis.
                </div>
              )}
            </div>
          </div>
        ) : (
          <p className="text-sm leading-relaxed text-brand-muted">
            Run a flow analysis to see drift, adequacy, gaps, and plots here.
          </p>
        )}
      </Section>

      <Section title="Pipe Setup">
        <div className="space-y-3">
          <Field label="Current pipe" value={formatPipe(workspace.pipeConfig)} />
          {pending ? (
            <div className="space-y-3 rounded-lg border border-amber-300/80 bg-amber-50/80 p-3 text-amber-950 dark:border-amber-900/70 dark:bg-amber-950/25 dark:text-amber-100">
              <div>
                <div className="text-xs font-semibold uppercase tracking-normal">
                  Confirmation required
                </div>
                <p className="mt-1 text-sm leading-relaxed">
                  {proposedLine(pending.proposed_values)}
                </p>
              </div>
              {pending.risk && (
                <p className="text-xs leading-relaxed text-amber-800 dark:text-amber-200">
                  {pending.risk}
                </p>
              )}
              <button
                type="button"
                disabled={processing || !pending.action_id}
                onClick={() => pending.action_id && onConfirmConfig(pending.action_id)}
                className="inline-flex min-h-9 w-full items-center justify-center rounded-md bg-amber-700 px-3 py-2 text-sm font-semibold text-white transition hover:bg-amber-800 disabled:cursor-not-allowed disabled:opacity-50"
              >
                Confirm and apply
              </button>
            </div>
          ) : workspace.lastConfig ? (
            <p className="rounded-md border border-brand-border/75 bg-white/60 px-3 py-2 text-xs leading-relaxed text-brand-muted dark:bg-white/[0.04]">
              Last configuration workflow: {workspace.lastConfig.status ?? "updated"}.
            </p>
          ) : null}
        </div>
      </Section>

      <Section title="Next Actions">
        <div className="flex flex-col gap-2">
          {(workspace.nextActions.length
            ? workspace.nextActions
            : ["Run health check", "Analyze recent flow", "Configure safely"]
          ).map((label) => (
            <button
              key={label}
              type="button"
              className="min-h-9 rounded-md border border-brand-border/80 bg-white px-3 py-2 text-left text-sm font-medium text-brand-800 transition hover:border-brand-400 hover:bg-brand-50 dark:bg-white/[0.04] dark:text-brand-900 dark:hover:bg-white/[0.08]"
              onClick={() => {
                const s = serial || "<METER SERIAL>";
                if (/flow|window|compare/i.test(label)) {
                  onCompose(`Analyze the last 24 hours of flow data for meter ${s}`);
                } else if (/pipe|configure/i.test(label)) {
                  onCompose(`Configure meter ${s} safely. I need to set pipe material, standard, size, and transducer angle.`);
                } else {
                  onCompose(`Run a health check on meter ${s}`);
                }
              }}
            >
              {label}
            </button>
          ))}
        </div>
      </Section>
    </div>
  );

  return (
    <>
      <div className="border-b border-brand-border/80 bg-brand-50/95 px-4 py-2 lg:hidden">
        <button
          type="button"
          className="flex w-full items-center justify-between rounded-lg border border-brand-border bg-white px-3 py-2 text-sm font-semibold text-brand-800 dark:bg-brand-100 dark:text-brand-900"
          onClick={() => setMobileOpen((v) => !v)}
          aria-expanded={mobileOpen}
        >
          <span>{hasMeter ? `Meter ${serial}` : "Meter workspace"}</span>
          <span aria-hidden>{mobileOpen ? "-" : "+"}</span>
        </button>
        {mobileOpen && (
          <div className="mt-2 max-h-[55dvh] overflow-hidden rounded-lg border border-brand-border bg-brand-50 shadow-lg">
            {body}
          </div>
        )}
      </div>
      <aside className="hidden h-full min-h-0 w-[22rem] shrink-0 border-l border-brand-border bg-brand-50/95 lg:flex lg:flex-col">
        <div className="border-b border-brand-border/80 px-4 py-4">
          <h1 className="text-sm font-semibold text-brand-900">Meter Workspace</h1>
          <p className="mt-1 text-xs leading-relaxed text-brand-muted">
            Live status, flow evidence, and safe configuration controls.
          </p>
        </div>
        {body}
      </aside>
    </>
  );
}
