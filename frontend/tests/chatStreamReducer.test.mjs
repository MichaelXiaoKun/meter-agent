import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { build } from "esbuild";

const outDir = join(tmpdir(), "bluebot-chat-stream-reducer-tests");
await mkdir(outDir, { recursive: true });
const outfile = join(outDir, `chatStreamReducer-${Date.now()}.mjs`);

await build({
  entryPoints: [new URL("../src/chatStreamReducer.ts", import.meta.url).pathname],
  outfile,
  bundle: true,
  format: "esm",
  platform: "node",
  logLevel: "silent",
});

const reducer = await import(`file://${outfile}?${Date.now()}`);

const state = reducer.createChatStreamState();
reducer.resetChatStreamStateForTurn(state, {
  streamId: "stream-1",
  turnId: "turn-1",
  cursor: 5,
});
const refs = {
  expectedTurnId: { current: "turn-1" },
  lastSeq: { current: 5 },
};

assert.deepEqual(
  reducer.applyStreamEventToChatState(
    state,
    { type: "text_delta", text: "ignored", turn_id: "turn-1", seq: 5 },
    refs,
  ),
  { applied: false },
);
assert.equal(state.streamLead, "");
assert.equal(state.cursor, 5);

assert.deepEqual(
  reducer.applyStreamEventToChatState(
    state,
    { type: "text_delta", text: "hello", turn_id: "turn-1", seq: 6 },
    refs,
  ),
  { applied: true, errorMessage: undefined },
);
assert.equal(state.streamLead, "hello");
assert.equal(state.cursor, 6);
assert.equal(refs.lastSeq.current, 6);

reducer.applyStreamEventToChatState(
  state,
  { type: "done", turn_id: "turn-1", seq: 7 },
  refs,
);
reducer.clearChatStreamStateAfterTurn(state);
assert.equal(state.streamId, null);
assert.equal(state.turnId, null);
assert.equal(state.cursor, 0);
assert.equal(state.streamLead, "");
assert.deepEqual(state.turnActivity, []);
assert.deepEqual(state.streamStatus, { kind: "idle" });

const waitState = reducer.createChatStreamState();
reducer.resetChatStreamStateForTurn(waitState, {
  streamId: "stream-2",
  turnId: "turn-2",
});
const waitRefs = {
  expectedTurnId: { current: "turn-2" },
  lastSeq: { current: 0 },
};
reducer.applyStreamEventToChatState(
  waitState,
  {
    type: "rate_limit_wait",
    turn_id: "turn-2",
    seq: 1,
    current_tokens: 51906,
    estimated_next_tokens: 55865,
    tpm_cap: 50000,
    overflow_tokens: 57771,
    waited_seconds: 0,
    message: "Waiting for input-token headroom",
  },
  waitRefs,
);
assert.deepEqual(waitState.streamStatus, {
  kind: "rate_limit_wait",
  message: "Waiting for input-token headroom",
});
assert.equal(waitState.turnActivity.at(-1).kind, "rate_limit_wait");
assert.equal(waitState.turnActivity.at(-1).title, "Waiting for rate-limit headroom");

const fleetState = reducer.createChatStreamState();
reducer.resetChatStreamStateForTurn(fleetState, {
  streamId: "stream-3",
  turnId: "turn-3",
});
const fleetRefs = {
  expectedTurnId: { current: "turn-3" },
  lastSeq: { current: 0 },
};
reducer.applyStreamEventToChatState(
  fleetState,
  {
    type: "tool_call",
    tool: "triage_fleet_for_account",
    input: { email: "aldbluebot@americanleakdetection.com" },
    turn_id: "turn-3",
    seq: 1,
  },
  fleetRefs,
);
assert.equal(fleetState.turnActivity.at(-1).title, "Triaging account fleet…");
assert.equal(fleetState.turnActivity.at(-1).details[0].label, "Account");

const validationState = reducer.createChatStreamState();
reducer.resetChatStreamStateForTurn(validationState, {
  streamId: "stream-4",
  turnId: "turn-4",
});
const validationRefs = {
  expectedTurnId: { current: "turn-4" },
  lastSeq: { current: 0 },
};
reducer.applyStreamEventToChatState(
  validationState,
  {
    type: "validation_start",
    message: "Checking evidence",
    turn_id: "turn-4",
    seq: 1,
  },
  validationRefs,
);
assert.deepEqual(validationState.streamStatus, {
  kind: "validation",
  message: "Checking evidence",
});
reducer.applyStreamEventToChatState(
  validationState,
  {
    type: "validation_result",
    verdict: "needs_experiment",
    next_action: "sweep_transducer_angles",
    message: "Angle diagnosis needs a controlled sweep.",
    turn_id: "turn-4",
    seq: 2,
  },
  validationRefs,
);
assert.equal(validationState.turnActivity.at(-1).kind, "validation");
assert.equal(validationState.turnActivity.at(-1).title, "Needs more evidence");

console.log("chatStreamReducer tests passed");
