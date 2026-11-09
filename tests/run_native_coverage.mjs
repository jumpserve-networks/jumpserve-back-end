// Only the fixed synthetic auth suites run; no native login or model call.
import {existsSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {checkReport} from './coverage-inventory.mjs';
const root=fileURLToPath(new URL('..',import.meta.url));
const pinned=new URL('../node_modules/node/bin/node',import.meta.url);
const node=existsSync(pinned)?fileURLToPath(pinned):process.execPath;
if(!process.argv.slice(2).every(value=>value==='--check'))throw new Error('Only --check is supported.');
const version=spawnSync(node,['--version'],{encoding:'utf8'});
if(version.status!==0||version.stdout.trim()!=='v24.16.0')throw new Error('Native unit coverage requires Node 24.16.0.');
const result=spawnSync(process.execPath,['node_modules/c8/bin/c8.js',node,'--test','research_workflow/tests/openclaw_auth_test.mjs','research_workflow/tests/openclaw_auth_entrypoint_test.mjs','research_workflow/tests/openclaw_auth_edges_test.mjs','tests/coverage-policy.test.mjs'],{cwd:root,stdio:'inherit'});
if(result.status!==0)process.exitCode=result.status??1;
else {
 const report=checkReport();
 console.log(JSON.stringify({inventory_valid:report.valid,target_met:report.complete,files:report.files,missing_files:report.missing,errors:report.errors,totals:report.totals}));
 process.exitCode=!report.valid?1:process.argv.includes('--check')&&!report.complete?2:0;
}
