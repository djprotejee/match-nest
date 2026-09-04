import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

const source = fs.readFileSync(new URL("../src/requestCache.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { ReadCache, refreshDelay } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);

test("concurrent requests share one fetch and reuse an empty result", async () => {
  const cache = new ReadCache();
  let calls = 0;
  const load = async () => { calls++; return []; };
  await Promise.all(Array.from({ length: 10 }, () => cache.read("account:calendar", 1000, load)));
  assert.deepEqual(await cache.read("account:calendar", 1000, load), []);
  assert.equal(calls, 1);
});

test("account and query keys are isolated", async () => {
  const cache = new ReadCache();
  assert.equal(await cache.read("a:calendar", 1000, async () => "a"), "a");
  assert.equal(await cache.read("b:calendar", 1000, async () => "b"), "b");
  assert.equal(await cache.read("a:calendar:spoilers", 1000, async () => "revealed"), "revealed");
});

test("mutation invalidation prevents a late stale response from repopulating cache", async () => {
  const cache = new ReadCache();
  let complete;
  const old = cache.read("entities", 1000, () => new Promise(resolve => { complete = resolve; }));
  cache.clear();
  assert.equal(await cache.read("entities", 1000, async () => "new"), "new");
  complete("old");
  await old;
  assert.equal(await cache.read("entities", 1000, async () => "unexpected"), "new");
});

test("failed requests can be retried", async () => {
  const cache = new ReadCache();
  await assert.rejects(cache.read("key", 1000, async () => { throw new Error("offline"); }));
  assert.equal(await cache.read("key", 1000, async () => "ok"), "ok");
});

test("expired responses are fetched again", async () => {
  const cache = new ReadCache();
  const original = Date.now;
  let now = 1000;
  Date.now = () => now;
  try {
    await cache.read("key", 100, async () => "old");
    now += 101;
    assert.equal(await cache.read("key", 100, async () => "new"), "new");
  } finally { Date.now = original; }
});

test("polling backs off and has a finite retry budget", () => {
  assert.deepEqual(Array.from({length: 7}, (_, index) => refreshDelay(index)), [10000,20000,30000,30000,30000,30000,null]);
});
