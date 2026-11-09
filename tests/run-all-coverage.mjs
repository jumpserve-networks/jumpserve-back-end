// Fixed local suites only. This command never deploys or invokes live DB tests.
import {mkdirSync, readdirSync, readFileSync, writeFileSync} from 'node:fs';
import {dirname, resolve, relative, extname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2);
if (args.some(arg => arg !== '--check')) throw new Error('Only --check is supported.');
const suites = [
  [
    "python",
    ".venv-unit-tests/bin/python",
    [
      "tests/run_unit_coverage.py"
    ],
    "coverage/python/summary.json"
  ],
  [
    "native",
    null,
    [
      "tests/run_native_coverage.mjs"
    ],
    "coverage/native/gate.json"
  ]
];
const results = [];
for (const [name, executable, arguments_, path] of suites) {
  const result = spawnSync(executable ? resolve(root, executable) : process.execPath, arguments_, {cwd: root, stdio: 'inherit'});
  let report = null;
  if (result.status === 0) report = JSON.parse(readFileSync(resolve(root, path), 'utf8'));
  results.push({name, passed: result.status === 0, target_met: Boolean(report && (report.complete ?? report.target_met)), report: path});
}
function inventory(directory) {
  return readdirSync(directory, {withFileTypes: true}).flatMap(entry => {
    const path = resolve(directory, entry.name);
    return entry.isDirectory() ? inventory(path) : entry.isFile() && ['.sh', '.sql'].includes(extname(entry.name)) ? [relative(root, path)] : [];
  });
}
const unmeasured = [].flatMap(directory => inventory(resolve(root, directory))).sort();
const summary = {generated_at: new Date().toISOString(), passed: results.every(suite => suite.passed),
  target_met: results.every(suite => suite.passed && suite.target_met) && !unmeasured.length, suites: results, unmeasured};
mkdirSync(resolve(root, 'coverage'), {recursive: true});
writeFileSync(resolve(root, 'coverage/summary.json'), JSON.stringify(summary, null, 2) + '\n');
console.log(JSON.stringify({...summary, unmeasured: undefined, unmeasured_file_count: unmeasured.length}));
process.exitCode = !summary.passed ? 1 : args.includes('--check') && !summary.target_met ? 2 : 0;

