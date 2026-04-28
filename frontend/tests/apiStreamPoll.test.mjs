import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import ts from "typescript";

const outDir = join(tmpdir(), "bluebot-api-stream-tests");
await mkdir(outDir, { recursive: true });

async function importTsModule(srcUrl, name) {
  const source = await readFile(srcUrl, "utf8");
  const transpiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
      verbatimModuleSyntax: true,
    },
  });
  const outPath = join(outDir, `${name}-${Date.now()}.mjs`);
  await writeFile(outPath, transpiled.outputText, "utf8");
  return import(`file://${outPath}`);
}

const api = await importTsModule(new URL("../src/api.ts", import.meta.url), "api");

const originalFetch = globalThis.fetch;
const urls = [];
const responses = [
  {
    events: [{ type: "thinking", seq: 6 }],
    done: false,
    next_cursor: 6,
  },
  {
    events: [{ type: "done", seq: 7 }],
    done: true,
    next_cursor: 7,
  },
];

globalThis.fetch = async (url) => {
  urls.push(String(url));
  const body = responses.shift();
  assert.ok(body, "unexpected extra poll");
  return {
    ok: true,
    json: async () => body,
  };
};

try {
  const events = [];
  await api.pollStream("stream-1", (event) => events.push(event), undefined, 5);

  assert.match(urls[0], /\/api\/streams\/stream-1\/poll\?cursor=5&/);
  assert.match(urls[1], /\/api\/streams\/stream-1\/poll\?cursor=6&/);
  assert.deepEqual(
    events.map((event) => event.type),
    ["thinking", "done"],
  );
} finally {
  globalThis.fetch = originalFetch;
}

console.log("api pollStream tests passed");

const originalEventSource = globalThis.EventSource;
const fallbackUrls = [];
const eventSources = [];
const fallbackResponses = [
  {
    ok: true,
    json: async () => ({ stream_id: "stream-fallback", turn_id: "turn-1" }),
  },
  {
    ok: true,
    json: async () => ({
      events: [{ type: "tool_progress", tool: "triage_fleet_for_account", message: "still working", seq: 6 }],
      done: false,
      next_cursor: 6,
    }),
  },
  {
    ok: true,
    json: async () => ({
      events: [{ type: "done", seq: 7 }],
      done: true,
      next_cursor: 7,
    }),
  },
];

class FakeEventSource {
  constructor(url) {
    this.url = url;
    this.closed = false;
    this.onmessage = null;
    this.onerror = null;
    eventSources.push(this);
  }

  close() {
    this.closed = true;
  }
}

globalThis.EventSource = FakeEventSource;
globalThis.fetch = async (url) => {
  fallbackUrls.push(String(url));
  const res = fallbackResponses.shift();
  assert.ok(res, "unexpected extra fallback fetch");
  return res;
};

try {
  const events = [];
  const streamed = api.streamChat(
    "conv-1",
    "hello",
    "token",
    (event) => events.push(event),
    undefined,
    "turn-1",
  );

  while (eventSources.length === 0) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }

  eventSources[0].onmessage({
    data: JSON.stringify({ type: "thinking", seq: 5, turn_id: "turn-1" }),
  });
  eventSources[0].onerror(new Error("dropped"));

  await streamed;

  assert.match(fallbackUrls[0], /\/api\/conversations\/conv-1\/chat$/);
  assert.match(fallbackUrls[1], /\/api\/streams\/stream-fallback\/poll\?cursor=5&/);
  assert.deepEqual(
    events.map((event) => event.type),
    ["thinking", "tool_progress", "done"],
  );
  assert.equal(eventSources[0].closed, true);
} finally {
  globalThis.fetch = originalFetch;
  globalThis.EventSource = originalEventSource;
}

console.log("api EventSource fallback tests passed");
