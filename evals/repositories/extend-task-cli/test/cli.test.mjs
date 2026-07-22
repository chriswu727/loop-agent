import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";

function run(...args) {
  return spawnSync(process.execPath, ["src/cli.mjs", ...args], { encoding: "utf8" });
}

test("filters open tasks and emits JSON only", () => {
  const result = run("list", "--status", "open", "--format", "json");
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), [
    { id: "T-1", title: "Ship docs", status: "open" },
    { id: "T-3", title: "Add tests", status: "open" },
  ]);
});

test("keeps text list behavior", () => {
  const result = run("list");
  assert.equal(result.status, 0);
  assert.match(result.stdout, /T-1\topen\tShip docs/);
});

test("rejects invalid format and missing status", () => {
  const badFormat = run("list", "--format", "yaml");
  assert.notEqual(badFormat.status, 0);
  assert.match(badFormat.stderr, /format/i);
  const missing = run("list", "--status");
  assert.notEqual(missing.status, 0);
  assert.match(missing.stderr, /status/i);
});
