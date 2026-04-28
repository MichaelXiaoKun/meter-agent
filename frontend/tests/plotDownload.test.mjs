import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import ts from "typescript";

const outDir = join(tmpdir(), "bluebot-plot-download-tests");
await mkdir(outDir, { recursive: true });
const plotDownloadUrl = new URL("../src/plotDownload.ts", import.meta.url);

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
const scheduled = [];
const opened = [];

globalThis.window = {
  location: { origin: "https://example.test" },
  setTimeout(fn, delay) {
    scheduled.push({ fn, delay });
    return scheduled.length;
  },
  open(url, target, features) {
    opened.push({ url, target, features });
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
      rel: "",
      click() {
        clicked.push({ href: this.href, download: this.download, rel: this.rel });
      },
      remove() {},
    };
  },
};
globalThis.URL.createObjectURL = (blob) => {
  assert.equal(blob.type, "image/png");
  return "blob:plot";
};
globalThis.URL.revokeObjectURL = (url) => {
  revoked.push(url);
};

let capturedFetch = null;
globalThis.fetch = async (url) => {
  capturedFetch = { url };
  return new Response(new Blob(["png"], { type: "image/png" }), {
    status: 200,
    headers: { "Content-Type": "image/png" },
  });
};

const mod = await importTsModule(plotDownloadUrl, "plotDownload");
assert.equal(
  mod.plotBasename("/api/plots/flow_meter_123.png?cache=1"),
  "flow_meter_123.png",
);

await mod.downloadPlotImage("/api/plots/flow_meter_123.png?cache=1");

assert.equal(capturedFetch.url, "/api/plots/flow_meter_123.png?cache=1");
assert.equal(clicked.length, 1);
assert.equal(clicked[0].href, "blob:plot");
assert.equal(clicked[0].download, "flow_meter_123.png");
assert.equal(clicked[0].rel, "noopener");
assert.equal(appended.length, 1);
assert.deepEqual(revoked, []);
assert.equal(scheduled.length, 1);
assert.equal(scheduled[0].delay, 0);
scheduled[0].fn();
assert.deepEqual(revoked, ["blob:plot"]);
assert.deepEqual(opened, []);

console.log("plotDownload tests passed");
