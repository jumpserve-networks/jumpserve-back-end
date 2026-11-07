// Real Node/SQLite/filesystem coverage of the production versioned install.
// All credentials and refreshes are synthetic; no network calls are made.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
import {DatabaseSync} from 'node:sqlite';

function fixture(t,expires=Date.now()+3600000) {
 const directory=fs.mkdtempSync(join(tmpdir(),'jumpserve-auth-entrypoint-'));
 t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
 const release=join(directory,'releases','version-1');fs.mkdirSync(release,{recursive:true});
 const source=fs.readFileSync(new URL('../openclaw_auth.mjs',import.meta.url));
 fs.writeFileSync(join(release,'openclaw_auth.mjs'),source);
 const runtime=join(directory,'runtime');fs.symlinkSync(release,runtime,'dir');
 const auth=join(directory,'auth');fs.mkdirSync(join(auth,'state'),{recursive:true});
 const database=join(auth,'state','openclaw.sqlite');
 const original={type:'oauth',provider:'openai',authFlow:'chatgpt-token-sharing',issuer:'https://auth.openai.com',accountId:'synthetic-account',clientId:'synthetic-client',access:'synthetic-access-old',refresh:'synthetic-refresh-old',expires,grantedScope:'resource.invoke chatgpt.tokens.use.direct'};
 const db=new DatabaseSync(database);db.exec('CREATE TABLE config_machine_state(state_key TEXT PRIMARY KEY,value_json TEXT)');
 db.prepare('INSERT INTO config_machine_state VALUES (?,?)').run('authProfiles.store',JSON.stringify({profiles:{main:original}}));db.close();
 const plugin=join(directory,'plugin.mjs'),calls=join(directory,'refresh-calls');
 fs.writeFileSync(plugin,`import {appendFileSync} from 'node:fs';export async function refreshTokenSharingCredential(c){appendFileSync(${JSON.stringify(calls)},'refresh\\n');return {...c,access:'synthetic-access-next',refresh:'synthetic-refresh-next',expires:Date.now()+3600000};}`);
 const helper=join(runtime,'openclaw_auth.mjs');
 return {directory,release,auth,database,plugin,calls,helper,original,
  run(path=helper){return spawnSync(process.execPath,[path,database,plugin],{timeout:10000});},
  read(){const d=new DatabaseSync(database);try{return JSON.parse(d.prepare('SELECT value_json FROM config_machine_state').get().value_json).profiles.main;}finally{d.close();}}};
}

test('directory and file symlink entrypoints return readiness without rotating a valid grant',t=>{
 const f=fixture(t),fileLink=join(f.directory,'auth-helper.mjs');fs.symlinkSync(join(f.release,'openclaw_auth.mjs'),fileLink);
 for(const path of [f.helper,fileLink]){
  const result=f.run(path);assert.equal(result.status,0,result.stderr.toString());
  assert.deepEqual(JSON.parse(result.stdout),{status:'ready'});
  assert.ok(!result.stdout.includes('synthetic-access'));assert.ok(!result.stderr.includes('synthetic-refresh'));
 }
 assert.deepEqual(f.read(),f.original);assert.equal(fs.existsSync(f.calls),false);
 assert.equal(fs.existsSync(join(f.auth,'refresh-recovery.bin')),false);
});

test('symlinked helper performs and persists one synthetic rotation, then reuses it after restart',t=>{
 const f=fixture(t,Date.now()-1);const first=f.run();assert.equal(first.status,0,first.stderr.toString());
 assert.deepEqual(JSON.parse(first.stdout),{status:'ready',refreshed:true});
 assert.equal(f.read().refresh,'synthetic-refresh-next');
 assert.equal(fs.readFileSync(f.calls,'utf8'),'refresh\n');
 const second=f.run();assert.equal(second.status,0,second.stderr.toString());
 assert.deepEqual(JSON.parse(second.stdout),{status:'ready'});assert.equal(fs.readFileSync(f.calls,'utf8'),'refresh\n');
 assert.ok(!Buffer.concat([first.stdout,first.stderr,second.stdout,second.stderr]).includes('synthetic-access'));
});

test('importing a symlinked helper does not execute its CLI or touch credentials',t=>{
 const f=fixture(t,Date.now()-1),before=fs.readFileSync(f.database);
 const code=`const module=await import(${JSON.stringify(new URL('file://'+f.helper).href)});if(typeof module.ensureCredential!=='function')throw Error('missing export');console.log('import-safe');`;
 const result=spawnSync(process.execPath,['--input-type=module','-e',code,f.database,f.plugin],{timeout:10000});
 assert.equal(result.status,0,result.stderr.toString());assert.equal(result.stdout.toString().trim(),'import-safe');
 assert.deepEqual(fs.readFileSync(f.database),before);assert.equal(fs.existsSync(f.calls),false);
 assert.equal(fs.existsSync(join(f.auth,'refresh-recovery.bin')),false);
});

test('uncertain rotation stays fenced when the same helper is restarted through a symlink',t=>{
 const f=fixture(t,Date.now()-1);
 fs.writeFileSync(f.plugin,`import {appendFileSync} from 'node:fs';export async function refreshTokenSharingCredential(){appendFileSync(${JSON.stringify(f.calls)},'refresh\\n');throw Error('synthetic transport uncertainty');}`);
 const first=f.run();assert.equal(first.status,1);assert.equal(JSON.parse(first.stdout).reason,'refresh-unconfirmed');
 const journal=fs.readFileSync(join(f.auth,'refresh-recovery.bin'));
 const second=f.run();assert.equal(second.status,1);assert.equal(JSON.parse(second.stdout).reason,'refresh-unconfirmed');
 assert.deepEqual(f.read(),f.original);assert.equal(fs.readFileSync(f.calls,'utf8'),'refresh\n');
 assert.deepEqual(fs.readFileSync(join(f.auth,'refresh-recovery.bin')),journal);
});
