// Test tooling: validate raw counters and source inventory, never rounded percentages.
export const metrics = ['lines', 'branches', 'functions', 'statements'];

export function validateCoverage(summary, expectedFiles) {
  const expected = new Set(expectedFiles);
  const files = Object.keys(summary ?? {}).filter(name => name !== 'total');
  const missing = [...expected].filter(name => !files.includes(name));
  const unexpected = files.filter(name => !expected.has(name));
  const errors = [], gaps = [];
  const totals = Object.fromEntries(metrics.map(metric => [metric, {covered: 0, total: 0}]));
  if (!expected.size) errors.push('Source inventory is empty.');
  if (expected.size !== expectedFiles.length) errors.push('Source inventory contains duplicate paths.');
  for (const name of files) {
    for (const metric of metrics) {
      const count = summary[name]?.[metric];
      if (!count || !Number.isSafeInteger(count.total) || !Number.isSafeInteger(count.covered) ||
          count.total < 0 || count.covered < 0 || count.covered > count.total ||
          (count.skipped !== undefined && count.skipped !== 0)) {
        errors.push(name + ': invalid ' + metric + ' counters');
        continue;
      }
      totals[metric].covered += count.covered;
      totals[metric].total += count.total;
      if (count.covered !== count.total) gaps.push({file: name, metric, missing: count.total - count.covered});
    }
  }
  for (const metric of metrics) {
    const aggregate = summary?.total?.[metric];
    if (!aggregate || aggregate.covered !== totals[metric].covered || aggregate.total !== totals[metric].total) {
      errors.push('Aggregate ' + metric + ' counters disagree with file counters.');
    }
  }
  const valid = !errors.length && !missing.length && !unexpected.length;
  return {valid, complete: valid && !gaps.length, files: files.length, missing, unexpected, errors, gaps, totals};
}

