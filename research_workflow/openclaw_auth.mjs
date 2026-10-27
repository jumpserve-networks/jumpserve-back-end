// Native SIWC refresh owner. No credential is ever returned to stdout/logs.
import {DatabaseSync} from 'node:sqlite';
import * as fs from 'node:fs';
import {createHash, randomUUID} from 'node:crypto';
import {dirname, resolve} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const SLOT = 131072, HEADER = 36, WINDOW = 900000, ATTEMPTS = 3;
const INVALID = new Set(['invalid_grant','invalid_refresh_token','token_expired','refresh_token_expired','refresh_token_invalidated','refresh_token_reused']);
const canonical = value => JSON.stringify(value, (_, v) => v && typeof v === 'object' && !Array.isArray(v) ? Object.fromEntries(Object.entries(v).sort(([a],[b]) => a.localeCompare(b))) : v);
const hash = value => createHash('sha256').update(canonical(value)).digest('hex');
export class AuthFailure extends Error {
  constructor(reason, status='unavailable', retryAfter=0) { super(reason); this.reason=reason; this.status=status; this.retryAfter=retryAfter; }
}
function credential(value) {
  const profiles=Object.entries(value.profiles??{});
  if (!profiles.length) throw new AuthFailure('credential-missing','auth-required');
  if (profiles.length!==1) throw new AuthFailure('client-configuration');
  const [id,c]=profiles[0];
  if(['accountId','clientId','access','refresh','grantedScope'].some(k=>typeof c[k]!=='string'||!c[k])||c.access.startsWith('sk-')||c.type!=='oauth'||c.provider!=='openai'||c.authFlow!=='chatgpt-token-sharing'||c.issuer!=='https://auth.openai.com'||!c.accountId||!c.clientId||c.clientId==='dynamic_agent_client'||!c.refresh||!c.access||!Number.isFinite(c.expires)) throw new AuthFailure('client-configuration');
  const scopes=new Set((c.grantedScope??'').split(/\s+/));
  if(!scopes.has('resource.invoke')||!scopes.has('chatgpt.tokens.use.direct')) throw new AuthFailure('permission-required');
  return [id,c];
}
function storageError(error) {
  return error instanceof AuthFailure ? error : new AuthFailure('storage-unavailable');
}
function writeAll(io,fd,buffer,position) {
  let offset=0;
  while(offset<buffer.length) {
    const n=io.writeSync(fd,buffer,offset,buffer.length-offset,position+offset);
    if(n<=0) throw new AuthFailure('storage-unavailable');
    offset+=n;
  }
  io.fsyncSync(fd);
}
function journal(io,path) {
  let fd;
  try {
    fd=io.openSync(path,io.constants.O_RDWR|io.constants.O_CREAT|io.constants.O_EXCL|io.constants.O_NOFOLLOW,0o600);
    writeAll(io,fd,Buffer.alloc(SLOT*2),0);
    const parent=io.openSync(dirname(path),'r');try{io.fsyncSync(parent);}finally{io.closeSync(parent);}
  } catch(error) {
    if(fd!==undefined){io.closeSync(fd);throw storageError(error);}
    if(error.code!=='EEXIST')throw storageError(error);
    fd=io.openSync(path,io.constants.O_RDWR|io.constants.O_NOFOLLOW);
  }
  try {
    const stat=io.fstatSync(fd);
    if(!stat.isFile()||stat.nlink!==1||stat.uid!==process.getuid()||stat.size!==SLOT*2)throw new AuthFailure('storage-unavailable');
    io.fchmodSync(fd,0o600);
    const read=index=>{
      const raw=Buffer.alloc(SLOT);let size=0;
      while(size<SLOT){const n=io.readSync(fd,raw,size,SLOT-size,index*SLOT+size);if(!n)break;size+=n;}
      if(!raw.some(b=>b))return null;
      const length=raw.readUInt32BE(0);
      if(length<1||length>SLOT-HEADER)return {corrupt:true};
      const body=raw.subarray(HEADER,HEADER+length);
      if(!createHash('sha256').update(body).digest().equals(raw.subarray(4,HEADER)))return {corrupt:true};
      try{return JSON.parse(body);}catch{return {corrupt:true};}
    };
    return {read,close:()=>io.closeSync(fd),write(index,value){
      const body=Buffer.from(JSON.stringify(value));if(body.length>SLOT-HEADER)throw new AuthFailure('credential-size');
      const raw=Buffer.alloc(SLOT);raw.writeUInt32BE(body.length,0);createHash('sha256').update(body).digest().copy(raw,4);body.copy(raw,HEADER);writeAll(io,fd,raw,index*SLOT);
    }};
  } catch(error){io.closeSync(fd);throw storageError(error);}
}
function rejection(error) {
  const detail=error?.oauthRefreshFailure??{};const code=detail.errorType??detail.reason??error?.code;
  if(INVALID.has(code))return {phase:'terminal',reason:'grant-invalid',status:'auth-required'};
  if(['invalid_client','ERR_MODULE_NOT_FOUND','ERR_PACKAGE_PATH_NOT_EXPORTED'].includes(code))return {phase:'backoff',reason:'client-configuration',status:'unavailable'};
  if(detail.status===429||detail.status>=500&&detail.status<=599)return {phase:'backoff',reason:'refresh-temporary',status:'unavailable'};
  if(Number.isInteger(detail.status)&&detail.status>=400&&detail.status<500)return {phase:'backoff',reason:'refresh-rejected',status:'unavailable'};
  // A transport/identity verification error can follow a successful token
  // rotation. Reusing that refresh token automatically is not safe.
  return {phase:'uncertain',reason:'refresh-unconfirmed',status:'unavailable'};
}

export async function ensureCredential({databasePath,refresh,now,clock=Date.now,force=false,io=fs,openDatabase=path=>new DatabaseSync(path)}) {
  const fixedNow=now,currentTime=()=>fixedNow??clock();now=currentTime();
  let db,log,transaction=false;
  try {
    const stat=io.lstatSync(databasePath);
    if(!stat.isFile()||stat.isSymbolicLink()||stat.uid!==process.getuid()||stat.nlink!==1)throw new AuthFailure('storage-unavailable');
    io.chmodSync(databasePath,0o600);
    db=openDatabase(databasePath);db.exec('PRAGMA busy_timeout=5000; PRAGMA synchronous=FULL; BEGIN IMMEDIATE');transaction=true;
    const row=db.prepare("SELECT value_json FROM config_machine_state WHERE state_key='authProfiles.store'").get();
    if(!row)throw new AuthFailure('credential-missing','auth-required');
    let value;try{value=JSON.parse(row.value_json);}catch{throw new AuthFailure('client-configuration');}
    let [id,current]=credential(value),base=hash(current);
    const logPath=resolve(dirname(databasePath),'..','refresh-recovery.bin');
    // Reading a valid unexpired grant does not require allocating recovery space.
    const exists=io.existsSync(logPath);
    if(!exists&&!force&&current.expires>now+90000)return {status:'ready'};
    log=journal(io,logPath);let intent=log.read(0),result=log.read(1);
    if(intent?.corrupt)throw new AuthFailure('storage-unavailable');
    if(intent?.base===base) {
      if(intent.phase==='pending'||intent.phase==='uncertain') {
        if(!result?.corrupt&&result?.nonce===intent.nonce&&result.base===base&&result.value) {
          const [nextId,next]=credential(result.value);
          if(nextId!==id||next.clientId!==current.clientId||next.accountId!==current.accountId||hash(next)!==result.next)throw new AuthFailure('client-configuration');
          const recovered={...value,profiles:{...value.profiles,[id]:next}};
          db.prepare("UPDATE config_machine_state SET value_json=? WHERE state_key='authProfiles.store'").run(JSON.stringify(recovered));
          db.exec('COMMIT');transaction=false;if(next.expires<=now+30000)throw new AuthFailure('refresh-backoff','unavailable',1);return {status:'ready',recovered:true};
        }
        throw new AuthFailure('refresh-unconfirmed');
      }
      if(intent.phase==='terminal')throw new AuthFailure(intent.reason,intent.status);
      if(intent.phase==='backoff'&&now<intent.nextAt)throw new AuthFailure('refresh-backoff','unavailable',Math.ceil((intent.nextAt-now)/1000));
    }
    if(!force&&current.expires>now+90000)return {status:'ready'};
    const space=io.statfsSync(dirname(databasePath));
    if(space.bavail*space.bsize<8*1024*1024)throw new AuthFailure('storage-low');
    // Force an actual SQLite write before asking OpenAI to rotate a token.
    db.prepare("UPDATE config_machine_state SET value_json=? WHERE state_key='authProfiles.store'").run(row.value_json.endsWith('\n')?row.value_json.trimEnd():row.value_json+'\n');
    const prior=intent?.base===base&&intent.phase==='backoff'&&now-intent.windowStart<WINDOW?intent:null;
    if(prior?.attempts>=ATTEMPTS)throw new AuthFailure('refresh-backoff','unavailable',Math.ceil((prior.windowStart+WINDOW-now)/1000));
    intent={version:1,phase:'pending',nonce:randomUUID(),base,attempts:(prior?.attempts??0)+1,windowStart:prior?.windowStart??now};
    log.write(0,intent);
    let next;
    try{next=await refresh(current);}catch(error){
      const failure=rejection(error);intent={...intent,...failure};
      const failedAt=currentTime();
      if(failure.phase==='backoff')intent.nextAt=intent.attempts>=ATTEMPTS?Math.max(intent.windowStart+WINDOW,failedAt+1000):failedAt+[10000,30000][intent.attempts-1];
      log.write(0,intent);
      throw new AuthFailure(failure.reason,failure.status,failure.phase==='backoff'?Math.max(1,Math.ceil((intent.nextAt-failedAt)/1000)):0);
    }
    const replacement={...value,profiles:{...value.profiles,[id]:next}};credential(replacement);
    if(next.clientId!==current.clientId||next.accountId!==current.accountId||next.expires<=currentTime()+30000)throw new AuthFailure('client-configuration');
    // Preallocated private result slot is flushed BEFORE SQLite commit. If the
    // commit fails, the next process restores this exact issued replacement.
    log.write(1,{version:1,nonce:intent.nonce,base,next:hash(next),value:replacement});
    db.prepare("UPDATE config_machine_state SET value_json=? WHERE state_key='authProfiles.store'").run(JSON.stringify(replacement));
    db.exec('COMMIT');transaction=false;
    return {status:'ready',refreshed:true};
  } catch(error) {
    if(error instanceof AuthFailure)throw error;
    if(error.code==='ENOENT'&&!db) {
      if(io.existsSync(resolve(dirname(databasePath),'..','refresh-recovery.bin')))throw new AuthFailure('storage-unavailable');
      throw new AuthFailure('credential-missing','auth-required');
    }
    throw storageError(error);
  } finally {
    if(transaction)try{db.exec('ROLLBACK');}catch{}
    if(db)try{db.close();}catch{}
    if(log)log.close();
  }
}

if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)) {
  const [databasePath,pluginPath,flag]=process.argv.slice(2);
  try {
    if(![4,5].includes(process.argv.length)||!databasePath||!pluginPath||flag&&flag!=='--force')throw new AuthFailure('client-configuration');
    const result=await ensureCredential({databasePath,force:flag==='--force',refresh:async c=>{
      const {refreshTokenSharingCredential}=await import(pathToFileURL(pluginPath).href);return refreshTokenSharingCredential(c);
    }});
    process.stdout.write(JSON.stringify(result));
  } catch(error) {
    const failure=storageError(error);
    process.stdout.write(JSON.stringify({status:failure.status,reason:failure.reason,retry_after_seconds:failure.retryAfter}));process.exitCode=1;
  }
}
