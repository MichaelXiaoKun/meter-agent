import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import ts from "typescript";

const outDir = join(tmpdir(), "bluebot-artifact-tests");
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
  const outPath = join(outDir, `${name}-${Date.now()}-${Math.random()}.mjs`);
  await writeFile(outPath, transpiled.outputText, "utf8");
  return import(`file://${outPath}`);
}

const mod = await importTsModule(
  new URL("../src/artifactAttachments.ts", import.meta.url),
  "artifactAttachments",
);

const single = mod.extractDownloadArtifacts([
  {
    type: "tool_result",
    content: JSON.stringify({
      download_artifacts: [
        {
          kind: "csv",
          title: "Flow data CSV",
          path: "/tmp/flow_data_BB1_1_2.csv",
          row_count: 12,
        },
      ],
    }),
  },
]);
assert.equal(single.length, 1);
assert.equal(single[0].filename, "flow_data_BB1_1_2.csv");
assert.equal(single[0].url, "/api/analysis-artifacts/flow_data_BB1_1_2.csv");
assert.equal(single[0].rowCount, 12);

const batch = mod.extractDownloadArtifacts([
  {
    type: "tool_result",
    content: JSON.stringify({
      meters: [
        {
          serial_number: "BB1",
          download_artifacts: [
            {
              kind: "csv",
              filename: "flow_data_BB1_1_2.csv",
              row_count: 2,
            },
          ],
        },
        {
          serial_number: "BB2",
          download_artifacts: [
            {
              kind: "csv",
              url: "/api/analysis-artifacts/flow_data_BB2_1_2.csv",
              rowCount: 3,
            },
          ],
        },
      ],
    }),
  },
]);
assert.deepEqual(
  batch.map((a) => [a.groupLabel, a.filename, a.rowCount]),
  [
    ["BB1", "flow_data_BB1_1_2.csv", 2],
    ["BB2", "flow_data_BB2_1_2.csv", 3],
  ],
);

assert.deepEqual(
  mod.extractDownloadArtifacts([{ type: "tool_result", content: "{bad json" }]),
  [],
);
assert.deepEqual(mod.extractDownloadArtifacts("plain text"), []);

const live = mod.artifactsFromEvent({
  type: "tool_result",
  tool: "analyze_flow_data",
  success: true,
  download_artifacts: [
    { kind: "csv", filename: "flow_data_BB3_1_2.csv", row_count: 9 },
  ],
});
assert.equal(live[0].filename, "flow_data_BB3_1_2.csv");

console.log("artifactAttachments tests passed");
