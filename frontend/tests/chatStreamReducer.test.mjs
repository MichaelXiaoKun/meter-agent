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

console.log("chatStreamReducer tests passed");
