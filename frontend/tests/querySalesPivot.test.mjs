import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/v2/components/sales-analyzer/querySalesPivot.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } });
const { querySalesPivot } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);

test('legacy API error is rejected instead of displayed as no data', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => Response.json({ ok: false, rows: [], error: 'Calculation failed' }));
  await assert.rejects(querySalesPivot('/query', {}, new AbortController().signal), /Calculation failed/);
});

test('transient hosted 503 retries once and returns the real rows', async (t) => {
  let calls = 0;
  t.mock.method(globalThis, 'fetch', async () => ++calls === 1
    ? new Response('Unavailable', { status: 503 })
    : Response.json({ ok: true, rows: [{ key: 'North' }] }));
  const result = await querySalesPivot('/query', {}, new AbortController().signal);
  assert.equal(calls, 2);
  assert.equal(result.rows[0].key, 'North');
});

test('cache warming 503 uses longer backoff then succeeds', async (t) => {
  let calls = 0;
  const waits = [];
  const realSetTimeout = globalThis.setTimeout;
  t.mock.method(globalThis, 'setTimeout', (fn, ms, ...args) => {
    waits.push(ms);
    return realSetTimeout(fn, 0, ...args);
  });
  t.mock.method(globalThis, 'fetch', async () => ++calls === 1
    ? Response.json({ detail: 'Sales Analyzer cache is warming. Please retry in a minute.' }, { status: 503 })
    : Response.json({ ok: true, rows: [{ key: 'Store A' }] }));
  const result = await querySalesPivot('/query', {}, new AbortController().signal);
  assert.equal(calls, 2);
  assert.equal(result.rows[0].key, 'Store A');
  assert.ok(waits.some((ms) => ms >= 10000), `expected long warming backoff, got ${waits}`);
});

test('cancelled old filter request is not retried', async (t) => {
  const controller = new AbortController();
  let calls = 0;
  t.mock.method(globalThis, 'fetch', async () => {
    calls++;
    controller.abort();
    throw new DOMException('Aborted', 'AbortError');
  });
  await assert.rejects(querySalesPivot('/query', {}, controller.signal), { name: 'AbortError' });
  assert.equal(calls, 1);
});

test('genuine empty success remains a valid result', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => Response.json({ ok: true, rows: [], total_records: 0 }));
  const result = await querySalesPivot('/query', {}, new AbortController().signal);
  assert.deepEqual(result.rows, []);
});
