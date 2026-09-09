const test = require('node:test');
const assert = require('node:assert/strict');
const { createPublishGuard } = require('../scripts/publish_guard.js');

test('reverted fill permits one distinct repair and one batched read per attempt', async () => {
  let value = 'old'; let reads = 0; let repair = false;
  const guard = createPublishGuard({ readFields: async () => { reads++; return { title: value }; } });
  const result = await guard.setField({ name: 'title', expected: 'new',
    write: async v => { value = v; }, settle: async () => { if (!repair) value = 'old'; },
    repair: async v => { repair = true; value = v; } });
  assert.equal(result.status, 'verified');
  assert.equal(result.attempts, 2); assert.equal(reads, 2);
});

test('persistent failure blocks later writes', async () => {
  let writes = 0;
  const guard = createPublishGuard({ readFields: async () => ({ title: 'old' }) });
  const field = { name: 'title', expected: 'new', write: async () => { writes++; },
    repair: async () => { writes++; }, settle: async () => {} };
  await assert.rejects(guard.setField(field), /失焦/);
  await assert.rejects(guard.setField(field), /已停止/);
  assert.equal(writes, 2);
});

test('exception does not replay uncertain mutations', async () => {
  let repairs = 0;
  const guard = createPublishGuard({ readFields: async () => ({}) });
  await assert.rejects(guard.setField({ name: 'title', expected: 'new',
    write: async () => { throw new Error('timeout'); }, settle: async () => {},
    repair: async () => { repairs++; } }), /timeout/);
  assert.equal(repairs, 0);
});

test('concurrent focus mutations are rejected', async () => {
  let release;
  const wait = new Promise(resolve => { release = resolve; });
  const guard = createPublishGuard({ readFields: async () => ({ title: 'new' }) });
  const field = { name: 'title', expected: 'new', write: async () => wait, settle: async () => {} };
  const first = guard.setField(field);
  await assert.rejects(guard.setField(field), /并发/);
  release(); await first;
});

test('next-field rerender reverting an earlier field is detected in the same snapshot', async () => {
  const values = {}; let reads = 0;
  const guard = createPublishGuard({ readFields: async () => { reads++; return { ...values }; } });
  await guard.setField({ name: 'title', expected: 'new', write: async v => { values.title = v; }, settle: async () => {} });
  await assert.rejects(guard.setField({ name: 'source', expected: 'url',
    write: async v => { values.source = v; values.title = 'old'; }, settle: async () => {} }), /回退/);
  assert.equal(reads, 2);
});

test('latency budget stops adding calls without promising cancellation', async () => {
  let time = 0; let settles = 0;
  const guard = createPublishGuard({ now: () => time, budgetMs: 100,
    readFields: async () => ({ title: 'new' }) });
  await assert.rejects(guard.setField({ name: 'title', expected: 'new',
    write: async () => { time = 101; }, settle: async () => { settles++; } }), /预算/);
  assert.equal(settles, 0);
});

test('absent field is never equal to the literal string undefined', async () => {
  const guard = createPublishGuard({ readFields: async () => ({}) });
  await assert.rejects(guard.setField({ name: 'title', expected: 'undefined',
    write: async () => {}, settle: async () => {} }), /失焦/);
});
