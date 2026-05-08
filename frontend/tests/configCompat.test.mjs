import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { build } from "esbuild";

const outDir = join(tmpdir(), "bluebot-config-compat-tests");
await mkdir(outDir, { recursive: true });
const outfile = join(outDir, `configCompat-${Date.now()}.mjs`);

await build({
  entryPoints: [new URL("../src/core/configCompat.ts", import.meta.url).pathname],
  outfile,
  bundle: true,
  format: "esm",
  platform: "node",
  logLevel: "silent",
});

const compat = await import(`file://${outfile}?${Date.now()}`);

assert.equal(
  compat.proposedConfigLine({
    serial_number: "BB1",
    action: "set_zero_point",
    mqtt_payload: { szv: "null" },
  }),
  'Set zero point -> {"szv":"null"}',
);

console.log("configCompat tests passed");
