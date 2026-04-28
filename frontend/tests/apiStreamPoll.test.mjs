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
