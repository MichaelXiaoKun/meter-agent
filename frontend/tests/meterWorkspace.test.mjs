import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { build } from "esbuild";

const outDir = join(tmpdir(), "bluebot-meter-workspace-tests");
await mkdir(outDir, { recursive: true });

async function importTsModule(srcUrl, name) {
  const outPath = join(outDir, `${name}-${Date.now()}.mjs`);
  await build({
    entryPoints: [srcUrl.pathname],
    outfile: outPath,
    bundle: true,
    format: "esm",
    platform: "node",
    logLevel: "silent",
  });
  return import(`file://${outPath}?${Date.now()}`);
}

const mod = await importTsModule(new URL("../src/core/meterWorkspace.ts", import.meta.url), "meterWorkspace");
const turnActivity = await importTsModule(new URL("../src/core/turnActivity.ts", import.meta.url), "turnActivity");
const configCopy = await importTsModule(new URL("../src/core/configWorkflowCopy.ts", import.meta.url), "configWorkflowCopy");

const baseMessages = [
  {
    role: "assistant",
    content: [
      {
        type: "turn_activity",
        events: [
          {
            type: "tool_result",
            tool: "get_meter_profile",
            success: true,
            meter_context: {
              serial_number: "BB1",
              label: "Kitchen",
              network_type: "wifi",
              timezone: "America/New_York",
            },
          },
          {
            type: "tool_result",
            tool: "analyze_flow_data",
            success: true,
            plot_paths: [
              "/tmp/BB1_1_time_series.png",
              "/tmp/BB1_1_signal_quality.png",
              "/tmp/BB1_1_diagnostic_timeline.png",
            ],
            plot_timezone: "America/New_York",
            plot_summaries: [
              {
                filename: "BB1_1_time_series.png",
                plot_type: "time_series",
                title: "Flow rate",
                plot_timezone: "America/New_York",
              },
              {
                filename: "BB1_1_signal_quality.png",
                plot_type: "signal_quality",
                title: "Signal quality",
                plot_timezone: "America/New_York",
              },
              {
                filename: "BB1_1_diagnostic_timeline.png",
                plot_type: "diagnostic_timeline",
                title: "Diagnostic timeline",
                plot_timezone: "America/New_York",
                caption: {
                  plot_type: "diagnostic_timeline",
                  summary: "The strongest interpretation is a real sustained upward flow change.",
                  diagnostic_markers: [
                    {
                      type: "drift",
                      label: "Upward drift alarm",
                      severity: "high",
                      timestamp: 1,
                      explanation: "CUSUM detected a sustained upward shift.",
                      source: "cusum_drift",
                    },
                  ],
                  next_actions: ["Explain this drift"],
                },
              },
            ],
            diagnostic_summary: {
              kind: "flow",
              range: "Yesterday",
              plot_count: 3,
              adequacy: {
                ok: true,
                actual_points: 43028,
                target_min: 200,
                gap_pct: 0,
              },
              attribution: {
                primary_type: "real_flow_change",
                severity: "high",
                confidence: "high",
                summary: "The strongest interpretation is a real sustained upward flow change.",
                next_checks: ["Compare against the previous day"],
              },
              drift: { cusum_ran: true, direction: "upward" },
              alarms: { up: 50, down: 0 },
              plot_explanation: {
                summary: "The diagnostic timeline highlights the first upward drift alarm.",
                markers: [
                  {
                    type: "drift",
                    label: "Upward drift alarm",
                    severity: "high",
                    timestamp: 1,
                    explanation: "CUSUM detected a sustained upward shift.",
                    source: "cusum_drift",
                  },
                ],
                next_actions: ["Explain this drift"],
              },
              next_actions: ["Check signal quality now"],
            },
          },
        ],
      },
    ],
  },
];

const state = mod.buildMeterWorkspace(baseMessages, []);
assert.equal(state.serialNumber, "BB1");
assert.equal(state.label, "Kitchen");
assert.equal(state.flow.range, "Yesterday");
assert.equal(state.flow.plots.length, 3);
assert.match(state.flow.adequacyExplanation, /43,028 samples available/);
assert.equal(mod.driftLabel(state.flow), "upward drift");
assert.equal(state.flow.attribution.primary_type, "real_flow_change");
assert.equal(mod.attributionLabel(state.flow.attribution), "Real flow change");
const diagnosticPlot = state.flow.plots.find((plot) => plot.plotType === "diagnostic_timeline");
assert.equal(diagnosticPlot.caption.diagnostic_markers[0].type, "drift");
assert.equal(diagnosticPlot.caption.next_actions[0], "Explain this drift");
assert.equal(state.flow.plotExplanation.markers[0].source, "cusum_drift");
assert.equal(state.nextActions[0], "Check signal quality now");

const pending = mod.buildMeterWorkspace([], [
  {
    type: "config_confirmation_required",
    tool: "set_transducer_angle_only",
    config_workflow: {
      action_id: "abc123",
      status: "pending_confirmation",
      serial_number: "BB1",
      proposed_values: { serial_number: "BB1", transducer_angle: "45" },
    },
  },
]);
assert.equal(pending.pendingConfig.action_id, "abc123");
const superseded = mod.buildMeterWorkspace([], [
  {
    type: "config_confirmation_required",
    tool: "set_transducer_angle_only",
    config_workflow: {
      action_id: "abc123",
      status: "pending_confirmation",
      serial_number: "BB1",
      proposed_values: { serial_number: "BB1", transducer_angle: "45" },
    },
  },
  {
    type: "config_confirmation_superseded",
    tool: "set_transducer_angle_only",
    config_workflow: {
      action_id: "abc123",
      status: "superseded",
      serial_number: "BB1",
      proposed_values: { serial_number: "BB1", transducer_angle: "45" },
    },
  },
]);
assert.equal(superseded.pendingConfig, null);
assert.equal(superseded.lastConfig.status, "superseded");

const streamOpened = { current: false };
let steps = turnActivity.reduceTurnActivity([], {
  type: "tool_call",
  tool: "set_transducer_angle_only",
  input: { serial_number: "BB1", transducer_angle: "45" },
}, streamOpened);
steps = turnActivity.reduceTurnActivity(steps, {
  type: "config_confirmation_required",
  tool: "set_transducer_angle_only",
  config_workflow: {
    action_id: "cfg1",
    status: "pending_confirmation",
    serial_number: "BB1",
    proposed_values: { serial_number: "BB1", transducer_angle: "45" },
  },
}, streamOpened);
assert.equal(steps.length, 1);
assert.equal(steps[0].phase, "waiting_confirmation");
assert.equal(steps[0].ok, true);
assert.match(steps[0].title, /Waiting for your confirmation/);
steps = turnActivity.reduceTurnActivity(steps, {
  type: "config_confirmation_superseded",
  tool: "set_transducer_angle_only",
  config_workflow: {
    action_id: "cfg1",
    status: "superseded",
    serial_number: "BB1",
    proposed_values: { serial_number: "BB1", transducer_angle: "45" },
  },
  message: "Replaced by your new request. No device change was sent.",
}, streamOpened);
assert.equal(steps.at(-1).phase, "done");
assert.match(steps.at(-1).title, /Replaced configuration review/);

const confirmText = configCopy.confirmationUserMessage({
  action_id: "cfg1",
  status: "pending_confirmation",
  serial_number: "BB1",
  proposed_values: { serial_number: "BB1", transducer_angle: "45" },
});
assert.equal(confirmText, "Yes, set meter BB1 to 45°.");
assert.doesNotMatch(confirmText, /cfg1|configuration action|action id/i);
assert.equal(
  configCopy.confirmationUserMessage({
    action_id: "cfg2",
    status: "pending_confirmation",
    tool: "configure_meter_pipe",
    serial_number: "BB2",
    proposed_values: {
      pipe_material: "PVC",
      pipe_standard: "SCH40",
      pipe_size: "2 in",
      transducer_angle: "45",
    },
  }),
  "Yes, apply the pipe configuration for meter BB2.",
);
assert.equal(
  configCopy.cancellationUserMessage(),
  "No, cancel this configuration change.",
);

console.log("meterWorkspace reducer tests passed");
