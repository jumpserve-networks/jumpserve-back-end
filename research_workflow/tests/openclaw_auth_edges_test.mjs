// Fault injection uses synthetic grants and local SQLite; no login/provider I/O.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
import {DatabaseSync} from 'node:sqlite';
import {createHash} from 'node:crypto';
import {ensureCredential, AuthFailure} from '../openclaw_auth.mjs';

const NOW = 1800000000000;
function fixture(t) {
  const directory = fs.mkdtempSync(join(tmpdir(), 'jumpserve-auth-edges-'));
  t.after(() => fs.rmSync(directory, {recursive: true, force: true}));
  fs.mkdirSync(join(directory, 'state'));
  const databasePath = join(directory, 'state/openclaw.sqlite');
  const grant = {type: 'oauth', provider: 'openai', authFlow: 'chatgpt-token-sharing', issuer: 'https://auth.openai.com', accountId: 'synthetic-account', clientId: 'synthetic-client', access: 'synthetic-access', refresh: 'synthetic-refresh', expires: NOW - 1, grantedScope: 'resource.invoke chatgpt.tokens.use.direct'};
  const db = new DatabaseSync(databasePath);
  db.exec('CREATE TABLE config_machine_state(state_key TEXT PRIMARY KEY,value_json TEXT)');
  db.prepare('INSERT INTO config_machine_state VALUES (?,?)').run('authProfiles.store', JSON.stringify({profiles: {main: grant}}));
  db.close();
  return {databasePath, directory, grant, write(value) {
    const db = new DatabaseSync(databasePath);
    try {db.prepare('UPDATE config_machine_state SET value_json=?').run(value);} finally {db.close();}
  }};
}
const replacement = grant => ({...grant, access: 'synthetic-next-access', refresh: 'synthetic-next-refresh', expires: NOW + 3600000});
const fail = (promise, reason) => assert.rejects(promise, error => error instanceof AuthFailure && error.reason === reason);
const run = (f, extra = {}) => ensureCredential({databasePath: f.databasePath, now: NOW, refresh: async () => {throw new Error('Provider must not be called');}, ...extra});

test('missing, malformed and ambiguous profile stores fail before any provider call', async t => {
  for (const [raw, reason] of [['{', 'client-configuration'], ['{}', 'credential-missing'], [JSON.stringify({profiles: {a: {}, b: {}}}), 'client-configuration']]) {
    const f = fixture(t); f.write(raw);
    await fail(run(f), reason);
  }
  const missing = fixture(t);
  const db = new DatabaseSync(missing.databasePath); db.exec('DELETE FROM config_machine_state'); db.close();
  await fail(run(missing), 'credential-missing');
});

test('invalid grant fields and unsupported identities fail locally', async t => {
  for (const change of [{access: 'sk-synthetic'}, {authFlow: 'other'}, {provider: 'other'}, {type: 'other'}, {issuer: 'https://other.test'}, {clientId: 'dynamic_agent_client'}, {expires: null}, {refresh: ''}]) {
    const f = fixture(t); f.write(JSON.stringify({profiles: {main: {...f.grant, ...change}}}));
    await fail(run(f), 'client-configuration');
  }
});

test('unsafe database and journal metadata stop before refresh', async t => {
  for (const change of [{isFile: () => false}, {isSymbolicLink: () => true}, {uid: process.getuid() + 1}, {nlink: 2}]) {
    const f = fixture(t);
    await fail(run(f, {io: {...fs, lstatSync: path => Object.assign(fs.lstatSync(path), change)}}), 'storage-unavailable');
  }
  const f = fixture(t);
  fs.writeFileSync(join(f.directory, 'refresh-recovery.bin'), 'truncated');
  await fail(run(f), 'storage-unavailable');
});

test('journal permission and zero-byte writes fail before refresh', async t => {
  const f = fixture(t);
  await fail(run(f, {io: {...fs, openSync() {throw Object.assign(new Error('private storage diagnostic'), {code: 'EACCES'});}}}), 'storage-unavailable');
  const g = fixture(t);
  await fail(run(g, {io: {...fs, writeSync() {return 0;}}}), 'storage-unavailable');
});

function writeSlot(path, index, body) {
  const raw = Buffer.alloc(131072), bytes = Buffer.from(body);
  raw.writeUInt32BE(bytes.length, 0);
  createHash('sha256').update(bytes).digest().copy(raw, 4); bytes.copy(raw, 36);
  const fd = fs.openSync(path, 'r+');
  try {fs.writeSync(fd, raw, 0, raw.length, index * raw.length); fs.fsyncSync(fd);} finally {fs.closeSync(fd);}
}

test('checksum-valid invalid JSON and checksum corruption are both rejected without replay', async t => {
  for (const malformedJSON of [true, false]) {
    const f = fixture(t);
    await fail(run(f, {refresh: async () => {throw {oauthRefreshFailure: {status: 503}};}}), 'refresh-temporary');
    const journal = join(f.directory, 'refresh-recovery.bin');
    writeSlot(journal, 0, malformedJSON ? '{' : '{"version":1}');
    if (!malformedJSON) {
      const fd = fs.openSync(journal, 'r+'); fs.writeSync(fd, Buffer.from([255]), 0, 1, 4); fs.closeSync(fd);
    }
    await fail(run(f), 'storage-unavailable');
  }
});

test('oversized replacement cannot erase the retained grant or be replayed automatically', async t => {
  const f = fixture(t);
  await fail(run(f, {refresh: async grant => ({...replacement(grant), grantedScope: grant.grantedScope + ' extra'.repeat(25000)})}), 'credential-size');
  await fail(run(f), 'refresh-unconfirmed');
});

test('recovered expired replacement persists its identity then returns backoff', async t => {
  const f = fixture(t);
  const openDatabase = path => {
    const db = new DatabaseSync(path);
    return {prepare: sql => db.prepare(sql), close: () => db.close(), exec(sql) {if (sql === 'COMMIT') throw new Error('synthetic commit failure'); db.exec(sql);}};
  };
  await fail(run(f, {openDatabase, refresh: async grant => replacement(grant)}), 'storage-unavailable');
  await assert.rejects(run(f, {now: NOW + 3599000}), error => error.reason === 'refresh-backoff' && error.retryAfter === 1);
  const db = new DatabaseSync(f.databasePath);
  try {assert.equal(JSON.parse(db.prepare('SELECT value_json FROM config_machine_state').get().value_json).profiles.main.refresh, 'synthetic-next-refresh');} finally {db.close();}
});

test('a preexisting trailing newline and forced refresh preserve the credential store', async t => {
  const f = fixture(t);
  f.write(JSON.stringify({profiles: {main: {...f.grant, expires: NOW + 3600000}}}) + '\n');
  let calls = 0;
  assert.deepEqual(await run(f, {force: true, refresh: async grant => {calls++; return replacement(grant);}}), {status: 'ready', refreshed: true});
  assert.equal(calls, 1);
});

test('invalid CLI argument counts and flags emit only typed failure diagnostics', t => {
  const f = fixture(t);
  for (const args of [[], [f.databasePath, 'synthetic-plugin', '--invalid']]) {
    const child = spawnSync(process.execPath, [new URL('../openclaw_auth.mjs', import.meta.url).pathname, ...args], {encoding: 'utf8'});
    assert.equal(child.status, 1);
    assert.deepEqual(JSON.parse(child.stdout), {status: 'unavailable', reason: 'client-configuration', retry_after_seconds: 0});
    assert.equal(child.stdout.includes(f.grant.refresh), false);
  }
});

test('original helper CLI returns readiness without exposing credentials', t => {
  const f = fixture(t);
  f.write(JSON.stringify({profiles: {main: {...f.grant, expires: Date.now() + 3600000}}}));
  const child = spawnSync(process.execPath, [new URL('../openclaw_auth.mjs', import.meta.url).pathname, f.databasePath, 'unused-plugin'], {encoding: 'utf8'});
  assert.equal(child.status, 0, child.stderr);
  assert.deepEqual(JSON.parse(child.stdout), {status: 'ready'});
  assert.equal(child.stdout.includes(f.grant.access), false);
});

test('a journal truncated after metadata validation cannot trigger another token rotation', async t => {
  const f = fixture(t);
  let calls = 0;
  await fail(run(f, {refresh: async () => {calls++; throw new Error('synthetic transport uncertainty');}}), 'refresh-unconfirmed');
  let truncate = true;
  const io = {...fs, readSync(fd, ...args) {
    if (truncate) {truncate = false; fs.ftruncateSync(fd, 0);}
    return fs.readSync(fd, ...args);
  }};
  await fail(run(f, {io, refresh: async grant => {calls++; return replacement(grant);}}), 'storage-unavailable');
  assert.equal(calls, 1);
});

function readSlot(path, index) {
  const raw = fs.readFileSync(path).subarray(index * 131072, (index + 1) * 131072);
  return JSON.parse(raw.subarray(36, 36 + raw.readUInt32BE(0)));
}

test('a recovery slot cannot substitute another profile, account, client or replacement hash', async t => {
  for (const change of ['profile', 'account', 'client', 'hash']) {
    const f = fixture(t);
    await fail(run(f, {refresh: async () => {throw new Error('synthetic uncertainty');}}), 'refresh-unconfirmed');
    const journal = join(f.directory, 'refresh-recovery.bin'), intent = readSlot(journal, 0);
    const grant = replacement(f.grant);
    if (change === 'account') grant.accountId = 'different-account';
    if (change === 'client') grant.clientId = 'different-client';
    writeSlot(journal, 1, JSON.stringify({nonce: intent.nonce, base: intent.base, next: 'wrong-hash',
      value: {profiles: {[change === 'profile' ? 'other-profile' : 'main']: grant}}}));
    await fail(run(f), 'client-configuration');
  }
});

test('an inconsistent backoff journal cannot exceed the attempt window', async t => {
  const f = fixture(t);
  await fail(run(f, {refresh: async () => {throw {oauthRefreshFailure: {status: 503}};}}), 'refresh-temporary');
  const journal = join(f.directory, 'refresh-recovery.bin'), intent = readSlot(journal, 0);
  writeSlot(journal, 0, JSON.stringify({...intent, attempts: 3, nextAt: NOW - 1}));
  await assert.rejects(run(f), error => error.reason === 'refresh-backoff' && error.retryAfter === 900);
});

test('rollback and close failures do not replace the primary credential failure', async t => {
  const f = fixture(t); f.write('{}');
  const openDatabase = path => {
    const db = new DatabaseSync(path);
    return {prepare: sql => db.prepare(sql), exec(sql) {
      if (sql === 'ROLLBACK') {db.exec(sql); throw new Error('private rollback detail');}
      db.exec(sql);
    }, close() {db.close(); throw new Error('private close detail');}};
  };
  await fail(run(f, {openDatabase}), 'credential-missing');
});

test('unstructured storage failures are normalized after cleanup without exposing their original values', async t => {
  for (const failure of [null, 'synthetic private storage failure', new Error('synthetic private storage detail')]) {
    const f = fixture(t);
    await fail(run(f, {io: {...fs, lstatSync() {throw failure;}}}), 'storage-unavailable');
  }
});

test('journal close failure after a committed replacement is typed and cannot rotate the replacement again', async t => {
  const f = fixture(t); let calls = 0;
  const io = {...fs, closeSync(fd) {fs.closeSync(fd); throw new Error('synthetic private close detail');}};
  // Restrict the fault to the journal close: journal creation itself
  // syncs and closes its parent directory before a provider call is permitted.
  let parentClosed = false;
  const closeIO = {...io, closeSync(fd) {
    if (!parentClosed) {parentClosed = true; return fs.closeSync(fd);}
    return io.closeSync(fd);
  }};
  await fail(run(f, {io: closeIO, refresh: async grant => {calls++; return replacement(grant);}}), 'storage-unavailable');
  assert.deepEqual(await run(f), {status: 'ready'});
  assert.equal(calls, 1);
});
