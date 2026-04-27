import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import ts from "typescript";

const outDir = join(tmpdir(), "bluebot-download-artifact-tests");
await mkdir(outDir, { recursive: true });
const apiUrl = new URL("../src/api.ts", import.meta.url);

async function importTsModule(srcUrl, name) {
  const source = await readFile(srcUrl, "utf8");
  const transpiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
      verbatimModuleSyntax: true,
    },
  });
  const outPath = join(outDir, `${name}-${Date.now()}-${Math.random()}.mjs`);
  await writeFile(outPath, transpiled.outputText, "utf8");
  return import(`file://${outPath}`);
}

const clicked = [];
const appended = [];
const revoked = [];

globalThis.window = {
  setTimeout(fn) {
    fn();
    return 1;
  },
};
globalThis.document = {
  body: {
    appendChild(node) {
      appended.push(node);
    },
  },
  createElement(tag) {
    assert.equal(tag, "a");
    return {
      href: "",
      download: "",
      style: {},
      click() {
        clicked.push({ href: this.href, download: this.download });
      },
      remove() {},
    };
  },
};
globalThis.URL = {
  createObjectURL(blob) {
    assert.equal(blob.type, "text/csv");
    return "blob:csv";
  },
  revokeObjectURL(url) {
    revoked.push(url);
  },
};

let capturedFetch = null;
globalThis.fetch = async (url, init) => {
  capturedFetch = { url, init };
  return new Response(new Blob(["timestamp\n1\n"], { type: "text/csv" }), {
    status: 200,
    headers: { "Content-Type": "text/csv" },
  });
};

const api = await importTsModule(apiUrl, "api");
await api.downloadArtifact(
  "/api/analysis-artifacts/flow_data_BB1_1_2.csv",
  "flow_data_BB1_1_2.csv",
  "token-123",
  "llm-key",
);

assert.equal(capturedFetch.url, "/api/analysis-artifacts/flow_data_BB1_1_2.csv");
assert.equal(capturedFetch.init.headers.Authorization, "Bearer token-123");
assert.equal(capturedFetch.init.headers["X-LLM-Key"], "llm-key");
assert.equal(clicked.length, 1);
assert.equal(clicked[0].href, "blob:csv");
assert.equal(clicked[0].download, "flow_data_BB1_1_2.csv");
assert.equal(appended.length, 1);
assert.deepEqual(revoked, ["blob:csv"]);

console.log("downloadArtifact tests passed");
