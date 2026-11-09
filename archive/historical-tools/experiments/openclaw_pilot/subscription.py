"""Local SIWC-only OpenClaw campaign. Never loads an API key or calls Bedrock.

The loopback bridge admits one actual Responses HTTP request per controller turn.
OpenClaw may retry its loopback connection; those retries cannot redispatch a
model call. Credentials stay in OpenClaw's separate private login directory.
"""
import argparse
import contextlib
import json
import os
from pathlib import Path
import sys
import sqlite3
import ssl
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from unittest.mock import patch

import run as lab

ROOT = lab.ROOT
BASE = ROOT / 'artifacts/v7'
AUTH = ROOT / '.subscription-auth'
PROTOCOL = ROOT / 'protocol-v7.json'
MIGRATION = lab.INFRA / 'database/202610200005_subscription_pilot.sql'
MODEL = 'gpt-6.1-sol'
ENDPOINT = 'https://api.openai.com/v1/responses'
CAFILE = ROOT / 'artifacts/documentation/v5/tls-ca.pem'
LIMIT_CODES = {'subscription_sharing_usage_limit_exceeded', 'subscription_sharing_usage_unavailable',
               'subscription_sharing_user_unavailable', 'rate_limit_exceeded', 'insufficient_quota'}
AUTH_CODES = {'subscription_sharing_user_not_eligible', 'subscription_sharing_invalid_user',
              'chatpass_v2_scope_not_authorized', 'chatpass_v2_invalid_authorization_context'}
PRICING = dict(version='chatgpt-subscription-unallocated-v1', model_id=MODEL,
               billing='chatgpt-subscription', measured_charges_usd=None, estimated_cost_usd=None,
               subscription_allocation_usd=None, orchestration_reserve_cents=100,
               limitation='Reserve is an orchestration allowance, not an API price or subscription charge.')


def protocol():
    value=json.loads(PROTOCOL.read_bytes())
    if value.get('base') != 'protocol-v5.json':
        raise ValueError('Unexpected subscription protocol base')
    return dict(json.loads((ROOT / value['base']).read_bytes()),**value)


@contextlib.contextmanager
def configuration():
    # The original Bedrock implementation and frozen revisions are not edited.
    with patch.object(lab, 'BASE', BASE), patch.object(lab.model, 'MODEL', MODEL), \
         patch.object(lab.model, 'PRICING', PRICING), \
         patch.object(lab.model, 'reservation', return_value=100), \
         patch.object(lab.model, 'estimate', return_value=None):
        yield


def environment():
    # An allowlist avoids inherited keys, alternate endpoints, proxies and auth.
    result = {k: os.environ[k] for k in ('PATH', 'HOME', 'TMPDIR', 'LANG', 'TERM') if k in os.environ}
    result.update(OPENCLAW_STATE_DIR=str(AUTH), OPENCLAW_CONFIG_PATH=str(AUTH / 'config.json'),
                  OPENCLAW_SKIP_CHANNELS='1', OPENCLAW_DISABLE_DISCOVERY='1', DO_NOT_TRACK='1')
    return result


def setup_auth():
    AUTH.mkdir(mode=0o700, parents=True, exist_ok=True)
    AUTH.chmod(0o700)
    target = AUTH / 'config.json'
    if not target.exists():
        lab.save(target, dict(agents=dict(defaults=dict(model=dict(primary='openai/' + MODEL, fallbacks=[]))),
                             plugins=dict(allow=['openai']), tools=dict(deny=['*'], toolSearch=False, codeMode=False)))
        target.chmod(0o600)


def login():
    """Official OpenClaw OAuth/PKCE and consent UI; never print credentials."""
    setup_auth()
    return subprocess.call([str(lab.NODE), str(lab.CLAW), 'models', 'auth', 'login',
                            '--provider', 'openai', '--method', 'siwc'], env=environment())


def validate_credential(value, now=None):
    now = time.time() if now is None else now
    scopes = set((value.get('grantedScope') or '').split())
    if (value.get('type') != 'oauth' or value.get('provider') != 'openai' or
        value.get('authFlow') != 'chatgpt-token-sharing' or value.get('issuer') != 'https://auth.openai.com' or
        not {'resource.invoke', 'chatgpt.tokens.use.direct'} <= scopes or
        not isinstance(value.get('access'), str) or not value['access'] or value['access'].startswith('sk-') or
        not isinstance(value.get('expires'), (int, float)) or isinstance(value.get('expires'), bool) or
        value['expires'] / 1000 <= now + 30):
        raise ValueError('Subscription authorization is missing, expired or lacks plan-use consent; run subscription.py login.')
    return value['access']


def credential():
    """Read only the dedicated native auth store; no personal ambient auth reuse."""
    stores = list((AUTH / 'agents').glob('*/agent/openclaw-agent.sqlite'))
    rows = []
    for path in stores:
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as connection:
            rows.extend(connection.execute("select store_json from auth_profile_store where store_key='primary'").fetchall())
    shared = AUTH / 'state/openclaw.sqlite'
    if shared.exists():
        with sqlite3.connect(shared.as_uri() + '?mode=ro', uri=True) as connection:
            rows.extend(connection.execute("select value_json from config_machine_state where state_key='authProfiles.store'").fetchall())
    profiles = []
    for (raw,) in rows:
        document = json.loads(raw)
        profiles.extend((document.get('profiles') or {}).values())
    if len(profiles) != 1:
        raise ValueError('Exactly one subscription credential is required; no profile or API-key fallback is allowed.')
    return validate_credential(profiles[0])


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Provider redirect refused; subscription bearer stays on the fixed endpoint.')


def tls_context():
    context=ssl.create_default_context(cafile=str(CAFILE))
    context.keylog_filename=None
    return context


def http_request(method, url, token, body=None):
    if (method, url) not in {('GET', 'https://api.openai.com/v1/models'), ('POST', ENDPOINT)}:
        raise ValueError('Only the official model catalog and Responses endpoint are allowed')
    request = urllib.request.Request(url, data=body, method=method,
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
                 'Accept': 'text/event-stream' if method == 'POST' else 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                        urllib.request.HTTPSHandler(context=tls_context()))
    try:
        return opener.open(request, timeout=30)
    except urllib.error.HTTPError as response:
        return response


def catalog():
    token = credential()
    directory = BASE / 'catalog' / lab.dt.datetime.now(lab.dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    directory.mkdir(parents=True, exist_ok=False)
    with http_request('GET', 'https://api.openai.com/v1/models', token) as response:
        raw = response.read(1048577)
        (directory / 'response.bin').write_bytes(raw)
        lab.save(directory / 'receipt.json', dict(http_status=response.status, sha256=lab.sha(raw),
                                                request_id=response.headers.get('x-request-id'), model_calls=0))
        if response.status != 200 or len(raw) > 1048576:
            raise ValueError('Subscription catalog unavailable; no model call was made.')
    document = json.loads(raw)
    if not any(row.get('slug') == MODEL and row.get('visibility') == 'list' for row in document.get('models', [])):
        raise ValueError('The exact frozen model is unavailable to this subscription; no model substitution.')
    return dict(path=str(directory.relative_to(BASE)), sha256=lab.sha(raw), model=MODEL, verified_at=lab.stamp())


def response_request(request):
    """Logical Bedrock-shaped controller data -> SIWC-compatible text request."""
    if request['modelId'] != MODEL:
        raise ValueError('Unexpected logical model')
    schema = request['toolConfig']['tools'][0]['toolSpec']['inputSchema']['json']
    instructions = '\n\n'.join(item['text'] for item in request['system'])
    instructions += '\nReturn exactly one JSON object satisfying this decision schema. No tools are available.\n' + lab.workflow.canonical(schema).decode()
    messages = []
    for message in request['messages']:
        if message['role'] not in ('user', 'assistant') or any(set(c) != {'text'} for c in message['content']):
            raise ValueError('Only text user/assistant messages are accepted')
        messages.append(dict(role=message['role'], content='\n'.join(c['text'] for c in message['content'])))
    return dict(model=MODEL, instructions=instructions, input=messages, store=False, stream=True,
                reasoning=dict(effort='medium'), text=dict(format=dict(type='json_object')), tools=[])


def usage(value):
    original = value.get('usage') if isinstance(value, dict) else None
    if not isinstance(original, dict):
        return None
    fields = {'inputTokens': 'input_tokens', 'outputTokens': 'output_tokens', 'totalTokens': 'total_tokens'}
    return {out: original.get(key) if type(original.get(key)) is int and original[key] >= 0 else None
            for out, key in fields.items()}


class StreamFailure(ValueError):
    def __init__(self, code, status='unknown', observed_usage=None):
        super().__init__(code)
        self.code, self.status, self.usage = code, status, observed_usage


def failure(code, status=None, observed_usage=None):
    state = 'paused-quota' if code in LIMIT_CODES or status == 429 else (
            'paused-auth' if code in AUTH_CODES or status in (401, 403) else 'failed')
    return StreamFailure(code or 'provider-error', state, observed_usage)


def read_stream(response, sink, deadline, maximum):
    """Persist every received byte before parsing. Completion requires the terminal event."""
    buffer = b''
    frame = []
    size = 0
    completed = None
    observed_usage = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise StreamFailure('provider-wall-limit', observed_usage=observed_usage)
        connection = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
        if connection is not None:
            connection.settimeout(min(30, remaining))
        chunk = response.read1(min(4096, maximum - size + 1))
        if not chunk:
            break
        sink.write(chunk); sink.flush()
        size += len(chunk)
        if size > maximum:
            raise StreamFailure('provider-response-byte-limit', observed_usage=observed_usage)
        buffer += chunk
        while b'\n' in buffer:
            line, buffer = buffer.split(b'\n', 1)
            line = line.rstrip(b'\r')
            if line:
                if line.startswith(b'data:'):
                    frame.append(line[5:].lstrip())
                continue
            if not frame:
                continue
            payload = b'\n'.join(frame); frame = []
            if payload == b'[DONE]':
                continue
            try:
                event = json.loads(payload)
            except (ValueError, UnicodeError):
                raise StreamFailure('malformed-provider-stream', observed_usage=observed_usage)
            kind = event.get('type')
            value = event.get('response') or {}
            observed_usage = usage(value) or observed_usage
            if kind in ('response.failed', 'response.incomplete', 'error'):
                error = value.get('error') or event.get('error') or event
                code = error.get('code') or kind
                raise failure(code, observed_usage=observed_usage) if kind != 'response.incomplete' else StreamFailure(code, observed_usage=observed_usage)
            if kind == 'response.completed':
                if completed is not None or value.get('status') != 'completed':
                    raise StreamFailure('invalid-terminal-response', observed_usage=observed_usage)
                completed = value
    if buffer or frame or completed is None:
        raise StreamFailure('incomplete-provider-stream', observed_usage=observed_usage)
    return completed


def structured_response(value):
    if value.get('model') != MODEL:
        raise ValueError('Provider reported a different model; no silent substitution')
    texts = []
    for item in value.get('output', []):
        if item.get('type') == 'reasoning':
            continue
        if item.get('type') != 'message' or item.get('role') != 'assistant':
            raise ValueError('Provider tool calls or unexpected output are prohibited')
        for content in item.get('content', []):
            if content.get('type') != 'output_text':
                raise ValueError('Provider refusal or non-text output')
            texts.append(content['text'])
    if len(texts) != 1 or len(texts[0].encode()) > 100000:
        raise ValueError('Expected one bounded JSON decision')
    decision = json.loads(texts[0])
    response = dict(stopReason='tool_use', output=dict(message=dict(role='assistant', content=[dict(toolUse=dict(
        name='submit_assessment_step', toolUseId='subscription-data-only', input=decision))])))
    response['usage'] = usage(value) or {}
    lab.model.parse(response)
    return response


class Provider(lab.Provider):
    def __init__(self, packet, token_source=credential, transport=http_request):
        super().__init__(packet)
        self.token_source, self.transport = token_source, transport
        self.stop_status = None
        self.last_start = None

    def openclaw(self, logical):
        self.logical = logical
        self.logical_response = None
        number = len(list((BASE / 'openclaw-runtime').glob('claw-turn-*'))) + 1
        directory = BASE / 'openclaw-runtime' / ('claw-turn-' + str(number).zfill(2))
        directory.mkdir(parents=True, exist_ok=False)
        workspace, state = directory / 'workspace', directory / 'state'
        workspace.mkdir(); state.mkdir()
        (workspace / 'AGENTS.md').write_text('\n\n'.join(b['text'] for b in logical['system']) +
            '\nReturn one JSON decision. The host validates the answer; no tools or shell access are available.\n')
        message = directory / 'message.json'
        message.write_text(logical['messages'][0]['content'][0]['text'])
        config = dict(models=dict(mode='replace', providers=dict(lab=dict(baseUrl=self.url + '/v1', apiKey=self.token,
            api='openai-completions', models=[dict(id=MODEL, name='Subscription pilot loopback adapter', reasoning=False,
            input=['text'], contextWindow=200000, maxTokens=4096,
            # Runtime requires numeric catalog fields. These are placeholders,
            # never interpreted or exported as measured subscription charges.
            cost=dict(input=0,output=0,cacheRead=0,cacheWrite=0))]))),
            agents=dict(defaults=dict(model=dict(primary='lab/' + MODEL, fallbacks=[]), workspace=str(workspace),
                skipBootstrap=True, bootstrapMaxChars=30000, bootstrapTotalMaxChars=40000,
                compaction=dict(enabled=False))),
            tools=dict(deny=['*'], toolSearch=False, codeMode=False, elevated=dict(enabled=False)),
            plugins=dict(enabled=False), skills=dict(allowBundled=[]))
        config_path = directory / 'config.json'; lab.save(config_path, config)
        env = environment()
        env.update(OPENCLAW_STATE_DIR=str(state),OPENCLAW_CONFIG_PATH=str(config_path))
        result = subprocess.run([str(lab.NODE),str(lab.CLAW),'agent','exec','--config',str(config_path),
            '--state-dir',str(state),'--cwd',str(workspace),'--message-file',str(message),
            '--model','lab/' + MODEL,'--code-mode','direct','--thinking','off','--timeout','180','--json'],
            capture_output=True,timeout=200,env=env)
        (directory / 'stdout.bin').write_bytes(result.stdout)
        (directory / 'stderr.bin').write_bytes(result.stderr)
        lab.save(directory / 'exit.json',dict(code=result.returncode,catalog_cost_fields='Non-billing placeholders; subscription allocation unknown.'))
        if result.returncode or self.logical_response is None:
            raise ValueError('OpenClaw turn did not complete; original CLI diagnostics retained.')
        envelope = json.loads(result.stdout); lab.save(directory / 'envelope.json',envelope)
        payloads = envelope.get('payloads') or envelope.get('result',{}).get('payloads') or []
        texts = [p['text'] for p in payloads if p.get('text')]
        if len(texts) != 1 or json.loads(texts[0]) != lab.model.parse(self.logical_response):
            raise ValueError('OpenClaw final answer differs from the retained subscription answer')
        return self.logical_response

    def call(self, request, wire=None):
        if wire is None:
            raise ValueError('Subscription pilot requires the actual OpenClaw request body')
        actual = response_request(request)
        raw, logical = lab.workflow.canonical(actual), lab.workflow.canonical(self.logical)
        limits = self.packet['protocol']['limits']
        if max(len(raw), len(logical), len(wire)) > limits['maximum_provider_request_bytes']:
            raise ValueError('Full request exceeds frozen byte limit; no model dispatch')
        # Validate authorization before admitting a model call. No API-key path.
        token = self.token_source()
        with lab.LOCK:
            if self.stop_status or time.monotonic() >= self.deadline:
                raise ValueError('Subscription campaign paused or its elapsed limit was reached')
            pid = lab.database.sql_text(self.packet['id'])
            state = json.loads(self.query("select json_build_object('count',count(*),'reserved',coalesce(sum(reserved_cents),0),'unsafe',count(*) filter(where status<>'complete')) from research_subscription_calls where pilot_id=" + pid))
            number = state['count'] + 1
            committed = self.packet['prior_committed_cents'] + limits['local_overhead_reserved_cents'] + state['reserved'] + 100
            if state['unsafe'] or number > limits['maximum_provider_calls'] or committed > limits['maximum_aggregate_committed_cents']:
                raise ValueError('A retained outcome or fixed resource limit prevents model admission')
            call_id = str(uuid.uuid4()); cid = lab.database.sql_text(call_id)
            self.query('insert into research_subscription_calls(id,pilot_id,number,logical_raw,logical_sha256,request_raw,request_sha256,transport_raw,transport_sha256,status,reserved_cents) values(' +
                ','.join([cid, pid, str(number), sql_bytes(logical), lab.database.sql_text(lab.sha(logical)), sql_bytes(raw),
                          lab.database.sql_text(lab.sha(raw)), sql_bytes(wire), lab.database.sql_text(lab.sha(wire)), "'running'", '100']) + ')')
        directory = BASE / 'openclaw-runtime' / ('provider-' + str(number).zfill(2))
        directory.mkdir(parents=True, exist_ok=False)
        for name, data in (('logical.json', logical), ('request.json', raw), ('openclaw-http-body.bin', wire)):
            (directory / name).write_bytes(data)
        status, code, http_status, request_id, observed, answer = 'unknown', None, None, None, None, None
        response_path = directory / 'response.bin'
        began = time.monotonic()
        try:
            spacing = limits['minimum_provider_start_spacing_seconds']
            if self.last_start is not None and time.monotonic() - self.last_start < spacing:
                time.sleep(spacing - (time.monotonic() - self.last_start))
            if time.monotonic() >= self.deadline:
                raise StreamFailure('campaign-deadline-before-dispatch', 'failed')
            self.last_start = time.monotonic()
            with response_path.open('wb') as sink, self.transport('POST', ENDPOINT, token, raw) as response:
                http_status = response.status
                request_id = response.headers.get('x-request-id')
                if http_status != 200:
                    body = response.read(limits['maximum_response_bytes'] + 1)
                    sink.write(body); sink.flush()
                    try:
                        document = json.loads(body)
                        code = (document.get('error') or {}).get('code')
                    except (ValueError, AttributeError):
                        code = None
                    raise failure(code or 'http-' + str(http_status), http_status)
                completed = read_stream(response, sink, min(self.deadline, self.last_start + limits['provider_timeout_seconds']),
                                        limits['maximum_response_bytes'])
                observed = usage(completed)
                try:
                    answer = structured_response(completed)
                except (ValueError, KeyError, TypeError):
                    raise StreamFailure('invalid-structured-decision', 'invalid-output', observed)
                status = 'complete'
        except StreamFailure as error:
            status, code, observed = error.status, error.code, error.usage or observed
        except Exception as error:
            code = 'unconfirmed-' + type(error).__name__
        finally:
            retained = response_path.read_bytes() if response_path.exists() else b''
            response_path.write_bytes(retained)
            received = http_status is not None
            self.query("update research_subscription_calls set status=" + lab.database.sql_text(status) +
                ',response_raw=' + (sql_bytes(retained) if received else 'null') + ',response_sha256=' + (lab.database.sql_text(lab.sha(retained)) if received else 'null') +
                ',usage=' + lab.database.sql_json(observed) + ',http_status=' + ('null' if http_status is None else str(http_status)) +
                ',error_code=' + ('null' if code is None else lab.database.sql_text(code)) +
                ',request_id=' + ('null' if request_id is None else lab.database.sql_text(request_id)) +
                ',ended_at=clock_timestamp() where id=' + cid)
            lab.save(directory / 'receipt.json', dict(status=status, error_code=code, http_status=http_status,
                request_id=request_id, usage=observed, wall_seconds=time.monotonic()-began,
                measured_charges_usd=None, subscription_allocation_usd=None, estimated_model_cents=None,
                reserved_orchestration_cents=100, provider_retries=0, paid_fallback=False,
                response_received=received,response_sha256=lab.sha(retained) if received else None))
        if status != 'complete':
            self.stop_status = status
            raise ValueError('Subscription campaign paused: ' + str(code) + '; retained response, no replay or paid fallback.')
        return answer


def sql_bytes(value):
    return "decode('" + value.hex() + "','hex')"


def source_files():
    return sorted(set([Path(__file__), PROTOCOL, ROOT / 'protocol-v5.json', ROOT / 'protocol-v6.json',
        ROOT / 'artifacts/v5/preparation-failure.json', ROOT / 'artifacts/v6/preflight-failure.json', CAFILE,
        MIGRATION, ROOT / 'test_subscription.py', ROOT / 'run.py',
        ROOT / 'results-v4.json', ROOT / 'runtime/package.json', ROOT / 'runtime/package-lock.json', lab.NODE, lab.CLAW,
        lab.INFRA / 'test/check_research_ideas_database.py', lab.INFRA / 'test/autonomy_fixtures.py',
        lab.INFRA / 'database/seed-autonomy-v8.json'] + list((lab.BACKEND / 'research_workflow').glob('*.py')) +
        list((lab.BACKEND / 'research_workflow/plans').glob('*.json')) +
        list((lab.BACKEND / 'research_workflow/policies').glob('*.json')) +
        list((ROOT / 'artifacts/documentation/v5').glob('*.md'))))


def freeze_file(packet, path):
    relative = str(path.relative_to(lab.BACKEND.parent))
    raw = path.read_bytes()
    packet['files'][relative] = lab.sha(raw)
    target = BASE / 'inputs' / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)


def prepare():
    if (BASE / 'frozen.json').exists():
        raise ValueError('Existing frozen subscription campaign must be preserved')
    campaign_protocol = protocol()
    previous = json.loads((ROOT / 'results-v4.json').read_bytes())
    if previous['aggregate']['conservative_reserved_cents'] != 2715:
        raise ValueError('Reconcile historical commitments before further work')
    if subprocess.check_output([str(lab.NODE), '--version'], text=True).strip() != 'v24.16.0' or \
       json.loads((lab.CLAW.parent / 'package.json').read_bytes())['version'] != '2026.10.5':
        raise ValueError('Pinned runtime differs')
    BASE.mkdir(parents=True, exist_ok=True)
    prior=2715+campaign_protocol['prior_local_preparation_reserved_cents']
    packet = dict(id=str(uuid.uuid4()), protocol=campaign_protocol, created_at=lab.stamp(), prior_committed_cents=prior, files={})
    gates = sorted((ROOT / 'artifacts/subscription-gates').glob('*/report.json'))
    if not gates:
        raise ValueError('Run subscription.py check before freezing a campaign')
    gate = json.loads(gates[-1].read_bytes())
    if not gate.get('passed') or any(lab.sha((lab.BACKEND.parent / name).read_bytes()) != digest for name,digest in gate['source_sha256'].items()):
        raise ValueError('The software gate failed or its tested source changed; preserve it and run a new check')
    packet['software_gate'] = dict(path=str(gates[-1].relative_to(ROOT)),sha256=lab.sha(gates[-1].read_bytes()))
    freeze_file(packet,gates[-1])
    for path in source_files():
        freeze_file(packet, path)
    f = lab.database
    f.DATABASE = 'jumpserve_research_ideas_test_sub_' + lab.dt.datetime.now(lab.dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ').lower()
    if len(f.DATABASE.encode())>63:
        raise ValueError('Local database identifier exceeds PostgreSQL limit')
    f.query.__defaults__ = (f.DATABASE, None)
    f.ACTOR = str(uuid.uuid4())
    f.DIRECTORY = BASE / 'openclaw-runtime/storage'; f.DIRECTORY.mkdir(parents=True, exist_ok=True)
    f.files.clear(); f.bootstrap()
    for name in f.report['migration_sha256']:
        freeze_file(packet, lab.INFRA / 'database' / name)
    f.query(MIGRATION.read_text())
    f.rpc('publish_agent_prompt', lab.autonomy_fixtures.publish_fixture())
    plan = json.loads((lab.BACKEND / 'research_workflow/plans/ipv6-archive-v1.json').read_bytes())
    with patch.object(lab.autonomy, 'wake', return_value={'status': 'local-subscription-pilot'}):
        study = lab.autonomy.start(f.ACTOR, dict(request_id=str(uuid.uuid4()), autonomous=True,
            paper=dict(title='OpenClaw subscription pilot v7 — IPv6 archived checks', paper_url=plan['paper_urls'][0],
                       domain='Internet measurement', study_kind='paper', scope=campaign_protocol['design'])))['id']
    intake = f.rpc('research_autonomy_control', dict(p_operation='lease', p_actor=None, p_study=None, p_payload={}))
    if intake['study_id'] != study or intake['stage'] != 'intake':
        raise ValueError('Unexpected intake identity')
    prepared = lab.preparation.prepare(lab.autonomy.one('studies', study, study), f.ACTOR,
                                      dict(action='prepare', request_id=str(uuid.uuid4())))
    claim = next(c for c in lab.autonomy.rows('claims', study) if c['description'] == campaign_protocol['source']['claim'])
    lab.autonomy.seed(intake, [claim], {'prepared_id': prepared['prepared']['id']})
    lab.autonomy.advance(intake, 'finished', reason='New subscription configuration scopes one archived claim.')
    task = next(t for t in lab.autonomy.rows('autonomy_tasks', study) if t['claim_id'] == claim['id'])
    blobs = {}
    for path, raw in f.files.items():
        destination = BASE / 'openclaw-runtime/originals' / lab.sha(raw)
        destination.parent.mkdir(parents=True, exist_ok=True); destination.write_bytes(raw)
        blobs[path] = dict(path=str(destination.relative_to(BASE)), sha256=lab.sha(raw))
    arm = dict(name='openclaw-runtime', database=f.DATABASE, actor=f.ACTOR, study_id=study, task_id=task['id'], claim_id=claim['id'], storage=blobs)
    packet.update(arm=arm, coordinator_database=f.DATABASE)
    raw = PROTOCOL.read_bytes()
    f.query('insert into research_subscription_pilots(id,study_id,protocol_raw,protocol_sha256,configuration,prior_committed_cents,overhead_cents,maximum_cents,status) values(' +
        ','.join([f.sql_text(packet['id']), f.sql_text(study), sql_bytes(raw), f.sql_text(lab.sha(raw)), f.sql_json(campaign_protocol['configuration']),
                  str(prior), '1000', '10000', "'prepared'"]) + ')')
    lab.save(BASE / 'frozen.json', packet)
    print(json.dumps(dict(frozen=str(BASE / 'frozen.json'), model_calls=0, prior_committed_cents=prior)))


def verify_frozen(packet):
    for name, digest in packet['files'].items():
        if lab.sha((lab.BACKEND.parent / name).read_bytes()) != digest:
            raise ValueError('Frozen subscription source changed: ' + name)


def verify_audit(provider, packet):
    records = json.loads(provider.query("select coalesce(json_agg(t),'[]') from (select *,encode(logical_raw,'hex') logical_hex,encode(request_raw,'hex') request_hex,encode(transport_raw,'hex') transport_hex,encode(response_raw,'hex') response_hex from research_subscription_calls where pilot_id=" +
        lab.database.sql_text(packet['id']) + ' order by number)t'))
    turns = json.loads((BASE / 'openclaw-runtime/autonomy_turns.json').read_bytes())
    links = {t['request_sha256'] for t in turns}
    count = 0
    # Do not export bytea rendering as a redundant SQL text representation.
    for row in records:
        directory = BASE / 'openclaw-runtime' / ('provider-' + str(row['number']).zfill(2))
        for field, name in (('logical', 'logical.json'), ('request', 'request.json'),
                            ('transport', 'openclaw-http-body.bin'), ('response', 'response.bin')):
            if row[field + '_hex'] is None:
                continue
            raw = bytes.fromhex(row[field + '_hex'])
            if lab.sha(raw) != row[field + '_sha256'] or (directory / name).read_bytes() != raw:
                raise ValueError('Stored original subscription bytes differ: ' + field)
            count += 1
        if row['logical_sha256'] not in links:
            raise ValueError('Original subscription prompt is not linked to its controller turn')
        for field in ('logical_raw', 'request_raw', 'transport_raw', 'response_raw'):
            row.pop(field, None)
    lab.save(BASE / 'database-original-byte-export.json', records)
    return dict(passed=True, calls=len(records), original_byte_records_verified=count, controller_prompt_links_verified=True)


def status():
    """Offline summary: no model/auth/catalog call, including after a pause."""
    target = BASE / 'report.json'
    if not target.exists():
        return dict(status='prepared' if (BASE / 'frozen.json').exists() else 'not-prepared',
                    model=MODEL, billing='chatgpt-subscription', paid_api_fallback=False, usage=None,
                    measured_charges_usd=None, subscription_allocation_usd=None)
    report = json.loads(target.read_bytes()); calls = report.get('calls',[])
    totals = {}
    for key in ('inputTokens','outputTokens','totalTokens'):
        values = [(call.get('usage') or {}).get(key) for call in calls]
        totals[key] = dict(known_sum=sum(v for v in values if type(v) is int),
                           missing_calls=sum(type(v) is not int for v in values))
    return dict(status='complete' if report.get('passed') else report.get('pause_status') or 'limited',
                model=MODEL,billing='chatgpt-subscription',paid_api_fallback=False,calls=len(calls),usage=totals,
                measured_charges_usd=None,subscription_allocation_usd=None,
                aggregate_committed_cents=report.get('aggregate_committed_cents'),
                covered_cells=report.get('covered_cells',0),planned_cells=1152,
                allowance_remaining=None,allowance_reset_at=None,
                usage_link='https://chatgpt.com/settings/usage',
                recovery='No automatic replay. A follow-up must preserve counters, commitments and paper deadlines.')


def check():
    """Stable scoped test entry point, append-only raw output and gate receipt."""
    directory = ROOT / 'artifacts/subscription-gates' / lab.dt.datetime.now(lab.dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    directory.mkdir(parents=True,exist_ok=False)
    sources = {str(path.relative_to(lab.BACKEND.parent)):lab.sha(path.read_bytes()) for path in source_files()}
    command = [sys.executable,'-B','-m','unittest','discover','-s','experiments/openclaw_pilot','-p','test_*.py','-v']
    began=time.monotonic()
    result=subprocess.run(command,cwd=lab.BACKEND,capture_output=True,timeout=180)
    (directory / 'stdout.bin').write_bytes(result.stdout);(directory / 'stderr.bin').write_bytes(result.stderr)
    report=dict(passed=result.returncode==0,exit_code=result.returncode,command=command,
        source_sha256=sources,stdout_sha256=lab.sha(result.stdout),stderr_sha256=lab.sha(result.stderr),
        wall_seconds=time.monotonic()-began,ended_at=lab.stamp(),actual_subscription_model_calls=0,
        cases='Development regressions, including actual local PostgreSQL and a real OpenClaw CLI against a synthetic provider.',
        reviewer=dict(identity='Codex primary implementer',type='AI',independent=False,human=False))
    lab.save(directory / 'report.json',report)
    print(json.dumps(dict(report=str(directory / 'report.json'),passed=report['passed'],model_calls=0)))
    if not report['passed']:
        raise SystemExit(1)


def live():
    packet = json.loads((BASE / 'frozen.json').read_bytes()); verify_frozen(packet)
    if (BASE / 'report.json').exists():
        raise ValueError('Campaign already attempted; preserve it and use a versioned follow-up')
    # Model catalog uses the same subscription token, no inference or AWS access.
    selected = catalog()
    provider = Provider(packet); pid = lab.database.sql_text(packet['id'])
    if provider.query('select status from research_subscription_pilots where id=' + pid) != 'prepared':
        raise ValueError('Campaign was already admitted; no replay')
    provider.query("update research_subscription_pilots set status='running',started_at=clock_timestamp() where id=" + pid)
    report = dict(version=packet['protocol']['id'], started_at=lab.stamp(), passed=False, catalog=selected,
                  production_mutations=0, provider=packet['protocol']['configuration'], measured_charges_usd=None,
                  subscription_allocation_usd=None, paid_api_fallback=False,
                  reviewer=dict(identity='Codex primary implementer', type='AI', independent=False, human=False))
    lab.save(BASE / 'report.json', report)
    http, thread = lab.server(provider)
    try:
        report['trajectory'] = lab.trajectory(provider, packet['arm'], packet)
        report['audit'] = verify_audit(provider, packet)
        measurements = json.loads((BASE / 'openclaw-runtime/measurements.json').read_bytes())
        oracle = bool(measurements)
        for row in measurements:
            with lab.localcontext() as ctx:
                ctx.prec = 100
                d = row['details']; difference = lab.Decimal(str(row['value'])) - lab.Decimal(d['published'])
                oracle &= row['status'] == 'recorded' and difference == lab.Decimal(d['difference']) and abs(difference) <= lab.Decimal(d['tolerance_absolute'])
        trajectory = report['trajectory']
        coverage = trajectory.get('assessment', {}).get('tested_conditions', {}).get('coverage', {})
        report.update(covered_cells=len(measurements), planned_cells=1152, decimal_oracle=oracle)
        report['passed'] = (trajectory.get('task_status') == 'resolved' and trajectory.get('label') == 'reproduced' and
                            coverage.get('covered_cells') == 1152 and len(measurements) == 1152 and oracle and not provider.stop_status)
    except Exception as error:
        report.update(error=type(error).__name__, reason=str(error))
    finally:
        http.shutdown(); thread.join(timeout=5); http.server_close()
        report['calls'] = json.loads(provider.query("select coalesce(json_agg(t),'[]') from (select number,status,usage,reserved_cents,estimated_cents,http_status,error_code,request_id,request_sha256,response_sha256,started_at,ended_at from research_subscription_calls where pilot_id=" + pid + ' order by number)t'))
        report.update(ended_at=lab.stamp(), pause_status=provider.stop_status,
            prior_committed_cents=packet['prior_committed_cents'],
            aggregate_committed_cents=packet['prior_committed_cents'] + 1000 + sum(c['reserved_cents'] for c in report['calls']),
            missing_usage_calls=sum(c['usage'] is None for c in report['calls']),
            original_protocol_sha256=lab.sha(PROTOCOL.read_bytes()),
            scope='Local lab only; one exposed claim and new model/provider. No matched runtime comparison, new Internet measurements or remote persistence verification.')
        lab.save(BASE / 'report.json', report)
        state = 'complete' if report['passed'] else 'paused' if provider.stop_status else 'limited'
        provider.query('update research_subscription_pilots set status=' + lab.database.sql_text(state) +
            ',ended_at=clock_timestamp(),report=' + lab.database.sql_json(report) + ' where id=' + pid)
        print(json.dumps(dict(report=str(BASE / 'report.json'), passed=report['passed'], calls=len(report['calls']), pause_status=provider.stop_status)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['login', 'catalog', 'check', 'prepare', 'run', 'status'])
    args = parser.parse_args()
    if args.action == 'login':
        raise SystemExit(login())
    elif args.action == 'catalog':
        print(json.dumps(catalog()))
    elif args.action == 'prepare':
        prepare()
    elif args.action == 'status':
        print(json.dumps(status()))
    elif args.action == 'check':
        check()
    else:
        with configuration():
            live()


if __name__ == '__main__':
    main()
