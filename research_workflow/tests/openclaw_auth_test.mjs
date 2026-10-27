// Synthetic grants/provider responses. Real SQLite, files and child processes.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
import {DatabaseSync} from 'node:sqlite';
import {ensureCredential,AuthFailure} from '../openclaw_auth.mjs';
const NOW=1800000000000;
function fixture(t,expires=NOW-1) {
 const directory=fs.mkdtempSync(join(tmpdir(),'jumpserve-auth-'));t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
 fs.mkdirSync(join(directory,'state'));const path=join(directory,'state/openclaw.sqlite');
 const old={type:'oauth',provider:'openai',authFlow:'chatgpt-token-sharing',issuer:'https://auth.openai.com',accountId:'synthetic-account',clientId:'synthetic-client',access:'synthetic-access-old',refresh:'synthetic-refresh-old',expires,grantedScope:'resource.invoke chatgpt.tokens.use.direct'};
 const db=new DatabaseSync(path);db.exec('CREATE TABLE config_machine_state(state_key TEXT PRIMARY KEY,value_json TEXT)');db.prepare('INSERT INTO config_machine_state VALUES (?,?)').run('authProfiles.store',JSON.stringify({profiles:{main:old}}));db.close();
 return {directory,path,old,read(){const d=new DatabaseSync(path);try{return JSON.parse(d.prepare('SELECT value_json FROM config_machine_state').get().value_json);}finally{d.close();}},write(c){const d=new DatabaseSync(path);try{d.prepare('UPDATE config_machine_state SET value_json=?').run(JSON.stringify({profiles:{main:c}}));}finally{d.close();}}};
}
const refreshed=c=>({...c,access:'synthetic-access-next',refresh:'synthetic-refresh-next',expires:NOW+3600000});
const run=(f,refresh,extra={})=>ensureCredential({databasePath:f.path,refresh,now:NOW,...extra});
async function fails(promise,reason,status='unavailable'){await assert.rejects(promise,e=>e instanceof AuthFailure&&e.reason===reason&&e.status===status);}

test('valid grant is reused; near-expiry rotation survives restart and preserves identity',async t=>{
 const f=fixture(t,NOW+3600000);let calls=0;const refresh=async c=>{calls++;return refreshed(c);};
 assert.equal((await run(f,refresh)).status,'ready');assert.equal(calls,0);
 f.write({...f.old,expires:NOW+1000});assert.equal((await run(f,refresh)).refreshed,true);assert.equal(calls,1);
 assert.equal((await run(f,refresh)).status,'ready');assert.equal(calls,1);assert.equal(f.read().profiles.main.clientId,f.old.clientId);assert.equal(f.read().profiles.main.refresh,'synthetic-refresh-next');
 assert.equal(fs.statSync(join(f.directory,'refresh-recovery.bin')).mode&0o777,0o600);assert.equal(fs.statSync(f.path).mode&0o777,0o600);
});
test('invalid grant is quarantined without repeated requests and a new sign-in clears it',async t=>{
 const f=fixture(t);let calls=0;const refresh=async()=>{calls++;throw Object.assign(new Error('do not log synthetic secret'),{oauthRefreshFailure:{errorType:'invalid_grant',status:400}});};
 await fails(run(f,refresh),'grant-invalid','auth-required');await fails(run(f,refresh),'grant-invalid','auth-required');assert.equal(calls,1);
 f.write({...f.old,access:'different-local-sign-in',refresh:'different-local-refresh',expires:NOW+3600000});assert.equal((await run(f,refresh)).status,'ready');assert.equal(calls,1);
});
test('confirmed temporary rejection backs off durably, with at most three attempts in fifteen minutes',async t=>{
 const f=fixture(t);let calls=0;const refresh=async()=>{calls++;throw Object.assign(new Error('private body'),{oauthRefreshFailure:{status:503}});};
 await fails(run(f,refresh),'refresh-temporary');await fails(run(f,refresh),'refresh-backoff');assert.equal(calls,1);
 await fails(run(f,refresh,{now:NOW+10000}),'refresh-temporary');await fails(run(f,refresh,{now:NOW+40000}),'refresh-temporary');assert.equal(calls,3);
 await fails(run(f,refresh,{now:NOW+600000}),'refresh-backoff');assert.equal(calls,3);
 await fails(run(f,refresh,{now:NOW+900001}),'refresh-temporary');assert.equal(calls,4);
});
test('429 is temporary; invalid client and missing plan permissions are configuration failures',async t=>{
 const f=fixture(t);await fails(run(f,async()=>{throw {oauthRefreshFailure:{status:429}};}),'refresh-temporary');
 const g=fixture(t);await fails(run(g,async()=>{throw {code:'invalid_client'};}),'client-configuration');
 const h=fixture(t);h.write({...h.old,grantedScope:'openid'});let calls=0;await fails(run(h,async()=>{calls++;}),'permission-required');assert.equal(calls,0);
});
test('unconfirmed transport/identity failures retain the grant and do not replay rotation',async t=>{
 const f=fixture(t);let calls=0;const refresh=async()=>{calls++;throw new TypeError('synthetic network reset');};
 await fails(run(f,refresh),'refresh-unconfirmed');await fails(run(f,refresh),'refresh-unconfirmed');assert.equal(calls,1);assert.equal(f.read().profiles.main.refresh,f.old.refresh);
});
test('low-space preflight refuses refresh before making any provider call',async t=>{
 const f=fixture(t);let calls=0;await fails(run(f,async()=>{calls++;},{io:{...fs,statfsSync:()=>({bavail:0,bsize:4096})}}),'storage-low');assert.equal(calls,0);
 assert.equal((await run(f,async c=>{calls++;return refreshed(c);})).status,'ready');assert.equal(calls,1);
});
test('SQLite failure after rotation recovers the issued replacement without token reuse',async t=>{
 const f=fixture(t);let calls=0;
 const openDatabase=path=>{const db=new DatabaseSync(path);return {prepare:s=>db.prepare(s),close:()=>db.close(),exec(s){if(s==='COMMIT')throw Object.assign(new Error('synthetic full disk'),{code:'SQLITE_FULL'});db.exec(s);}};};
 await fails(run(f,async c=>{calls++;return refreshed(c);},{openDatabase}),'storage-unavailable');assert.equal(f.read().profiles.main.refresh,f.old.refresh);
 assert.equal((await run(f,async()=>{calls++;throw Error('must not refresh twice');})).recovered,true);assert.equal(calls,1);assert.equal(f.read().profiles.main.refresh,'synthetic-refresh-next');
});
test('actual process exit after journal fsync recovers on a fresh process',async t=>{
 const f=fixture(t);const script=`import {DatabaseSync} from 'node:sqlite';import {ensureCredential} from ${JSON.stringify(new URL('../openclaw_auth.mjs',import.meta.url).href)};await ensureCredential({databasePath:process.argv[1],now:${NOW},refresh:async c=>({...c,access:'restarted-access',refresh:'restarted-refresh',expires:${NOW+3600000}}),openDatabase:p=>{const d=new DatabaseSync(p);return{prepare:s=>d.prepare(s),close:()=>d.close(),exec:s=>{if(s==='COMMIT')process.exit(23);d.exec(s);}}}});`;
 const child=spawnSync(process.execPath,['--input-type=module','-e',script,f.path]);assert.equal(child.status,23,child.stderr.toString());
 assert.equal((await run(f,async()=>{throw Error('no replay');})).recovered,true);assert.equal(f.read().profiles.main.refresh,'restarted-refresh');
});
test('a replacement that cannot reach the recovery slot is uncertain, never an automatic sign-in request',async t=>{
 const f=fixture(t);let calls=0;const io={...fs,writeSync(fd,buffer,offset,length,position){if(position===131072&&buffer.length===131072)throw Object.assign(Error('synthetic full disk'),{code:'ENOSPC'});return fs.writeSync(fd,buffer,offset,length,position);}};
 await fails(run(f,async c=>{calls++;return refreshed(c);},{io}),'storage-unavailable');await fails(run(f,async()=>{calls++;}),'refresh-unconfirmed');assert.equal(calls,1);
});
test('recovery cannot overwrite a fresh manual sign-in or resurrect signed-out credentials',async t=>{
 const f=fixture(t);const openDatabase=p=>{const d=new DatabaseSync(p);return{prepare:s=>d.prepare(s),close:()=>d.close(),exec:s=>{if(s==='COMMIT')throw Error('synthetic failed commit');d.exec(s);}};};
 await fails(run(f,async c=>refreshed(c),{openDatabase}),'storage-unavailable');
 f.write({...f.old,access:'newer-manual-login',refresh:'newer-manual-refresh',expires:NOW+3600000});await run(f,async()=>{throw Error('no refresh');});assert.equal(f.read().profiles.main.access,'newer-manual-login');
 const db=new DatabaseSync(f.path);db.prepare('UPDATE config_machine_state SET value_json=?').run('{"profiles":{}}');db.close();await fails(run(f,async()=>{}),'credential-missing','auth-required');
});
test('client/account changes and corrupt recovery state fail closed without repeated refresh',async t=>{
 const f=fixture(t);await fails(run(f,async c=>({...refreshed(c),accountId:'other-account'})),'client-configuration');await fails(run(f,async()=>{throw Error('no replay');}),'refresh-unconfirmed');
 const g=fixture(t);await fails(run(g,async()=>{throw {oauthRefreshFailure:{status:503}};}),'refresh-temporary');
 const fd=fs.openSync(join(g.directory,'refresh-recovery.bin'),'r+');fs.writeSync(fd,Buffer.from('broken'),0,6,0);fs.closeSync(fd);await fails(run(g,async()=>{}),'storage-unavailable');
});
test('recovery preallocation failure occurs before calling the provider',async t=>{
 const f=fixture(t);let calls=0;const io={...fs,writeSync(){throw Object.assign(Error('full'),{code:'ENOSPC'});}};await fails(run(f,async()=>{calls++;},{io}),'storage-unavailable');assert.equal(calls,0);
});

test('a later uncertain rotation cannot reuse a prior successful recovery slot',async t=>{
 const f=fixture(t);await run(f,async c=>refreshed(c));let calls=0;
 await fails(run(f,async()=>{calls++;throw Error('synthetic lost token response');},{now:NOW+3599000}),'refresh-unconfirmed');
 await fails(run(f,async()=>{calls++;},{now:NOW+3599001}),'refresh-unconfirmed');assert.equal(calls,1);assert.equal(f.read().profiles.main.refresh,'synthetic-refresh-next');
});
test('missing prior database is a storage failure; initial missing credentials require setup',async t=>{
 const f=fixture(t);await run(f,async c=>refreshed(c));fs.unlinkSync(f.path);await fails(run(f,async()=>{}),'storage-unavailable');
 const g=fixture(t);fs.unlinkSync(g.path);await fails(run(g,async()=>{}),'credential-missing','auth-required');
});
test('actual helper diagnostics omit secret-bearing exception text',t=>{
 const f=fixture(t,Date.now()-1);const plugin=join(f.directory,'synthetic-plugin.mjs');const secret='synthetic-secret-never-log';
 fs.writeFileSync(plugin,`export async function refreshTokenSharingCredential(){throw Object.assign(Error('${secret}'),{oauthRefreshFailure:{status:503}});}`);
 const result=spawnSync(process.execPath,[new URL('../openclaw_auth.mjs',import.meta.url).pathname,f.path,plugin]);assert.equal(result.status,1);assert.equal(JSON.parse(result.stdout).status,'unavailable');assert.ok(!result.stdout.includes(secret));assert.ok(!result.stderr.includes(secret));
});

test('repaired client configuration resumes after bounded backoff; an untyped 400 does not prove invalid grant',async t=>{
 const f=fixture(t);await fails(run(f,async()=>{throw {code:'invalid_client'};}),'client-configuration');
 assert.equal((await run(f,async c=>refreshed(c),{now:NOW+10000})).status,'ready');
 const g=fixture(t);await fails(run(g,async()=>{throw {oauthRefreshFailure:{status:400}};}),'refresh-rejected');
});

test('backoff starts after a slow failed refresh, rather than before its response',async t=>{
 const f=fixture(t);let clock=NOW;
 await assert.rejects(ensureCredential({databasePath:f.path,clock:()=>clock,refresh:async()=>{clock+=45000;throw {oauthRefreshFailure:{status:503}};}}),e=>e.reason==='refresh-temporary'&&e.retryAfter===10);
 await fails(run(f,async()=>{throw Error('too early');},{now:clock+9999}),'refresh-backoff');
 assert.equal((await run(f,async c=>refreshed(c),{now:clock+10000})).status,'ready');
});
