import assert from 'node:assert/strict';
import {test} from 'node:test';
import {metrics, validateCoverage} from './coverage-policy.mjs';
const report = (covered = 1, total = 1) => {
  const counters = Object.fromEntries(metrics.map(metric => [metric, {covered, total, pct: 100}]));
  return {total: structuredClone(counters), '/source.ts': structuredClone(counters)};
};

test('coverage requires every source file, including unloaded files', () => {
  const result = validateCoverage(report(), ['/source.ts', '/unloaded.ts']);
  assert.equal(result.valid, false);
  assert.equal(result.complete, false);
  assert.deepEqual(result.missing, ['/unloaded.ts']);
});
test('rounded 100 percent cannot conceal an uncovered line or branch', () => {
  const result = validateCoverage(report(99999, 100000), ['/source.ts']);
  assert.equal(result.valid, true);
  assert.equal(result.complete, false);
  assert.deepEqual(result.gaps.map(gap => gap.missing), [1, 1, 1, 1]);
});
test('exact counters pass, including legitimately branchless files', () => {
  assert.equal(validateCoverage(report(), ['/source.ts']).complete, true);
  assert.equal(validateCoverage(report(0, 0), ['/source.ts']).complete, true);
});
test('missing, malformed, skipped and inconsistent counters fail validation', () => {
  for (const change of [
    value => {delete value['/source.ts'].branches;},
    value => {value['/source.ts'].lines.covered = 2;},
    value => {value['/source.ts'].lines.skipped = 1;},
    value => {value['/source.ts'].lines.total = 1.5;},
    value => {value.total.lines.total = 2;},
    value => {delete value.total;},
  ]) {
    const value = report(); change(value);
    assert.equal(validateCoverage(value, ['/source.ts']).valid, false);
  }
  assert.equal(validateCoverage({}, []).complete, false);
  assert.equal(validateCoverage(report(), ['/source.ts', '/source.ts']).valid, false);
  assert.equal(validateCoverage(report(), ['/other.ts']).valid, false);
});

