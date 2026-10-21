"""Fixed lab comparison: actual OpenClaw, Bedrock, controller, SQL and arithmetic.

Local identity/Storage/prompt activation are explicitly fixtures. No production
mutation, deployment, shell-capable agent or new Internet experiment is allowed.
"""
import argparse
import copy
import datetime as dt
from decimal import Decimal, localcontext
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT.parents[1]
INFRA = BACKEND.parent / 'jumpserve-infra'
sys.path[:0] = [str(BACKEND / 'research_workflow'), str(INFRA / 'test')]
import autonomy
import autonomy_model as model
import preparation
import runner
import store
import worker
import workflow
import check_research_ideas_database as database
import autonomy_fixtures

BASE = ROOT / 'artifacts/v2'
PROTOCOL = ROOT / 'protocol-v2.json'
MIGRATION = INFRA / 'database/202610200003_runtime_pilot_audit.sql'
NODE = ROOT / 'runtime/node_modules/node/bin/node'
CLAW = ROOT / 'runtime/node_modules/openclaw/openclaw.mjs'
ARM_NAMES = ('jumpserve-direct', 'openclaw-runtime')
LOCK = threading.Lock()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(workflow.canonical(value))


def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def load_protocol():
    value = json.loads(PROTOCOL.read_bytes())
    if value.get('base'):
        if value['base'] != 'protocol-v1.json':
            raise ValueError('Unexpected protocol base')
        value = dict(json.loads((ROOT / value['base']).read_bytes()), **value)
    return value


def validate_arms(packet):
    if [a['name'] for a in packet['arms']] != list(ARM_NAMES) or len({a['study_id'] for a in packet['arms']}) != 2:
        raise ValueError('The two fixed comparison arm identities are required')


def source_files():
    paths = [PROTOCOL, ROOT / 'protocol-v1.json', Path(__file__), ROOT / 'test_pilot.py', MIGRATION,
             ROOT / 'runtime/package.json', ROOT / 'runtime/package-lock.json',
             INFRA / 'test/check_research_ideas_database.py', INFRA / 'test/autonomy_fixtures.py',
             INFRA / 'research-workflow-runtime.json', NODE, CLAW]
    paths += sorted((BACKEND / 'research_workflow').glob('*.py'))
    paths += sorted((BACKEND / 'research_workflow/plans').glob('*.json'))
    paths += sorted((BACKEND / 'research_workflow/policies').glob('*.json'))
    paths += [INFRA / 'database/seed-autonomy-v8.json']
    return paths


def activate(arm, packet):
    database.DATABASE = arm['database']
    database.ACTOR = arm['actor']
    database.query.__defaults__ = (arm['database'], None)
    database.DIRECTORY = BASE / arm['name'] / 'storage'
    database.DIRECTORY.mkdir(parents=True, exist_ok=True)
    database.files.clear()
    for path, entry in arm['storage'].items():
        raw = (BASE / entry['path']).read_bytes()
        if sha(raw) != entry['sha256']:
            raise ValueError('Frozen Storage fixture differs')
        database.files[path] = raw
    store.request = database.transport


def prepare():
    target = BASE / 'frozen.json'
    if target.exists():
        raise ValueError('Frozen pilot already exists; preserve it and create a new protocol version.')
    protocol = load_protocol()
    if subprocess.check_output([str(NODE), '--version'], text=True).strip() != 'v24.16.0':
        raise ValueError('Pinned Node runtime differs')
    if json.loads((CLAW.parent / 'package.json').read_bytes())['version'] != '2026.10.5':
        raise ValueError('Pinned OpenClaw runtime differs')
    BASE.mkdir(parents=True, exist_ok=True)
    packet = dict(id=str(uuid.uuid4()), created_at=stamp(), protocol=protocol, files={}, arms=[])
    if protocol.get('prior_campaign'):
        prior_path = ROOT / protocol['prior_campaign']
        prior = json.loads(prior_path.read_bytes())
        if prior['calls'] or prior['reserved_cents'] != protocol['prior_reserved_cents']:
            raise ValueError('Prior campaign commitments differ; reconcile before further work')
        packet['prior_report_sha256'] = sha(prior_path.read_bytes())
    for path in source_files():
        name = str(path.relative_to(BACKEND.parent))
        raw = path.read_bytes()
        packet['files'][name] = sha(raw)
        copy_to = BASE / 'inputs' / name
        copy_to.parent.mkdir(parents=True, exist_ok=True)
        copy_to.write_bytes(raw)
    plan_path = BACKEND / 'research_workflow/plans/ipv6-archive-v1.json'
    plan = json.loads(plan_path.read_bytes())
    for arm_name in ARM_NAMES:
        suffix = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ').lower()
        database.DATABASE = 'jumpserve_research_ideas_test_openclaw_' + suffix
        database.query.__defaults__ = (database.DATABASE, None)
        database.ACTOR = str(uuid.uuid4())
        database.DIRECTORY = BASE / arm_name / 'storage'
        database.DIRECTORY.mkdir(parents=True, exist_ok=True)
        database.files.clear()
        database.bootstrap()
        for migration_name, digest in database.report['migration_sha256'].items():
            migration_path = INFRA / 'database' / migration_name
            relative = str(migration_path.relative_to(BACKEND.parent))
            packet['files'][relative] = digest
            destination = BASE / 'inputs' / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(migration_path.read_bytes())
        database.rpc('publish_agent_prompt', autonomy_fixtures.publish_fixture())
        with patch.object(autonomy, 'wake', return_value={'status': 'local-pilot'}):
            created = autonomy.start(database.ACTOR, dict(request_id=str(uuid.uuid4()), autonomous=True,
                paper=dict(title='OpenClaw paired runtime pilot: IPv6 archived checks',
                    paper_url=plan['paper_urls'][0], domain='Internet measurement', study_kind='paper',
                    scope='Only the six retained absolute folded metrics checks; no new measurements.')))
        study = created['id']
        intake = database.rpc('research_autonomy_control', dict(p_operation='lease', p_actor=None, p_study=None, p_payload={}))
        if intake['study_id'] != study or intake['stage'] != 'intake':
            raise ValueError('Unexpected local claim intake')
        prepared = preparation.prepare(autonomy.one('studies', study, study), database.ACTOR,
                                       dict(action='prepare', request_id=str(uuid.uuid4())))
        claim = next(c for c in autonomy.rows('claims', study) if c['description'] == protocol['source']['claim'])
        autonomy.seed(intake, [claim], {'prepared_id': prepared['prepared']['id']})
        autonomy.advance(intake, 'finished', reason='Pilot explicitly scopes one archived claim.')
        task = next(t for t in autonomy.rows('autonomy_tasks', study) if t['claim_id'] == claim['id'])
        blobs = {}
        for path, raw in database.files.items():
            destination = BASE / arm_name / 'originals' / sha(raw)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
            blobs[path] = dict(path=str(destination.relative_to(BASE)), sha256=sha(raw))
        packet['arms'].append(dict(name=arm_name, database=database.DATABASE, actor=database.ACTOR,
            study_id=study, task_id=task['id'], claim_id=claim['id'], storage=blobs))
    validate_arms(packet)
    packet['coordinator_database'] = packet['arms'][0]['database']
    database.query(MIGRATION.read_text(), database=packet['coordinator_database'])
    raw = PROTOCOL.read_bytes()
    database.query("insert into research_runtime_pilots(id,protocol_raw,protocol_sha256,configuration,status,maximum_cents,overhead_cents) values(" +
        database.sql_text(packet['id']) + ",decode('" + raw.hex() + "','hex')," + database.sql_text(sha(raw)) + ',' +
        database.sql_json(protocol['configuration']) + ",'prepared',10000,1000)", database=packet['coordinator_database'])
    save(target, packet)
    print(json.dumps(dict(frozen=str(target), sha256=sha(target.read_bytes()), arms=2, model_calls=0)))


def verify_frozen(packet):
    validate_arms(packet)
    for name, expected in packet['files'].items():
        if sha((BACKEND.parent / name).read_bytes()) != expected:
            raise ValueError('Frozen source differs: ' + name)
    if packet.get('prior_report_sha256') and sha((ROOT / packet['protocol']['prior_campaign']).read_bytes()) != packet['prior_report_sha256']:
        raise ValueError('Original failed campaign report differs')


class Provider:
    def __init__(self, packet, smoke=False):
        self.packet = packet
        self.smoke = smoke
        self.arm = None
        self.logical = None
        self.logical_response = None
        self.deadline = time.monotonic() + packet['protocol']['limits']['maximum_elapsed_seconds']
        self.smoke_requests = []

    def query(self, sql):
        return database.query(sql, database=self.packet['coordinator_database'])

    def call(self, request, wire=None):
        raw = workflow.canonical(request)
        if len(raw) > 96000 or wire is not None and len(wire) > 96000:
            raise ValueError('Complete request exceeds frozen 96000-byte bound; no provider call.')
        if request['modelId'] != model.MODEL or request['inferenceConfig'] != {'maxTokens': 4096, 'temperature': 0}:
            raise ValueError('Fixed model configuration differs')
        if self.smoke:
            self.smoke_requests.append(request)
            value = dict(action='blocked', reason='missing-required-inputs', blockers=[dict(kind='missing-data',
                         detail='missing-source-data', required_input='Synthetic smoke check has no scientific input.')], claims=[], check_id=None,
                         proposal=None, ready=None, coverage_complete=False, assessment_label=None, evidence_ids=[])
            return dict(stopReason='tool_use', output=dict(message=dict(role='assistant', content=[dict(toolUse=dict(
                name='submit_assessment_step', toolUseId='smoke-only', input=value))])),
                usage=dict(inputTokens=100, outputTokens=30, totalTokens=130))
        with LOCK:
            if self.arm not in ARM_NAMES:
                raise ValueError('Unknown comparison arm; no provider call')
            if time.monotonic() >= self.deadline:
                raise ValueError('Frozen pilot elapsed limit reached')
            pid = database.sql_text(self.packet['id'])
            row = json.loads(self.query("select json_build_object('count',count(*),'reserved',coalesce(sum(reserved_cents),0),'unsafe',count(*) filter(where status in ('running','unknown','failed'))) from research_runtime_pilot_calls where pilot_id=" + pid))
            if row['unsafe']:
                raise ValueError('An earlier provider call needs reconciliation; no automatic replay.')
            arm = database.sql_text(self.arm)
            number = int(self.query('select count(*)+1 from research_runtime_pilot_calls where pilot_id=' + pid + ' and arm=' + arm))
            reserved = model.reservation(request)
            prior = self.packet['protocol'].get('prior_reserved_cents', 0)
            if number > 25 or row['reserved'] + reserved + 1000 + prior > 10000:
                raise ValueError('Frozen shared budget or 25-call arm limit reached')
            call_id = str(uuid.uuid4())
            cid = database.sql_text(call_id)
            self.query("insert into research_runtime_pilot_calls(id,pilot_id,arm,number,logical_request_sha256,request_raw,request_sha256,transport_raw,transport_sha256,status,reserved_cents) values(" +
                ','.join([cid, pid, arm, str(number), database.sql_text(sha(workflow.canonical(self.logical))),
                          "decode('" + raw.hex() + "','hex')", database.sql_text(sha(raw)),
                          "decode('" + wire.hex() + "','hex')" if wire is not None else 'null',
                          database.sql_text(sha(wire)) if wire is not None else 'null', "'running'", str(reserved)]) + ')')
        directory = BASE / self.arm / ('provider-' + str(number).zfill(2))
        directory.mkdir(parents=True, exist_ok=False)
        request_path = directory / 'request.json'
        request_path.write_bytes(raw)
        if wire is not None:
            (directory / 'openclaw-http-body.bin').write_bytes(wire)
        try:
            result = subprocess.run(['aws', 'bedrock-runtime', 'converse', '--profile', 'jumpserve', '--region', 'us-east-1',
                '--cli-input-json', 'file://' + str(request_path), '--cli-connect-timeout', '5', '--cli-read-timeout', '90', '--output', 'json'],
                capture_output=True, timeout=120, env={**os.environ, 'AWS_MAX_ATTEMPTS': '1', 'AWS_RETRY_MODE': 'standard', 'AWS_PAGER': ''})
            (directory / 'response.bin').write_bytes(result.stdout)
            (directory / 'stderr.bin').write_bytes(result.stderr)
            self.query("update research_runtime_pilot_calls set response_raw=decode('" + result.stdout.hex() + "','hex'),response_sha256=" +
                       database.sql_text(sha(result.stdout)) + ' where id=' + cid)
            if result.returncode:
                self.query("update research_runtime_pilot_calls set status='failed',ended_at=clock_timestamp(),reason='Provider failed; retained private diagnostics and commitment, no replay.' where id=" + cid)
                raise ValueError('Provider failed or unconfirmed; no replay')
            response = json.loads(result.stdout)
            model.parse(response)
            usage = response.get('usage')
            estimate = model.estimate(usage)
            if estimate is not None and estimate > reserved:
                raise ValueError('Observed estimated model charge exceeds admission reservation')
            self.query("update research_runtime_pilot_calls set status='complete',response_raw=decode('" + result.stdout.hex() + "','hex'),response_sha256=" +
                database.sql_text(sha(result.stdout)) + ',usage=' + database.sql_json(usage) + ',estimated_cents=' +
                ('null' if estimate is None else str(estimate)) + ',ended_at=clock_timestamp() where id=' + cid)
            return response
        except Exception:
            self.query("update research_runtime_pilot_calls set status='unknown',ended_at=clock_timestamp(),reason=coalesce(reason,'No confirmed valid answer; commitment retained, no replay.') where id=" + cid + " and status='running'")
            raise

    def openclaw(self, logical):
        self.logical = logical
        self.logical_response = None
        number = len(list((BASE / self.arm).glob('claw-turn-*'))) + 1
        directory = BASE / self.arm / ('claw-turn-' + str(number).zfill(2))
        directory.mkdir(parents=True, exist_ok=False)
        workspace = directory / 'workspace'
        state = directory / 'state'
        workspace.mkdir(); state.mkdir()
        (workspace / 'AGENTS.md').write_text('\n\n'.join(b['text'] for b in logical['system']) +
            '\nReturn one JSON decision as instructed. The host validates the structured provider answer; no tools or shell access are available.\n')
        message = directory / 'message.json'
        message.write_text(logical['messages'][0]['content'][0]['text'])
        config = dict(models=dict(mode='replace', providers=dict(lab=dict(baseUrl=self.url + '/v1', apiKey=self.token,
            api='openai-completions', models=[dict(id=model.MODEL, name='Pinned lab model', reasoning=False,
            input=['text'], contextWindow=200000, maxTokens=4096, cost=dict(input=8, output=40, cacheRead=8, cacheWrite=8))]))),
            agents=dict(defaults=dict(model=dict(primary='lab/' + model.MODEL), workspace=str(workspace),
                skipBootstrap=True, bootstrapMaxChars=30000, bootstrapTotalMaxChars=40000,
                compaction=dict(enabled=False))),
            tools=dict(deny=['*'], toolSearch=False, codeMode=False, elevated=dict(enabled=False)),
            plugins=dict(enabled=False), skills=dict(allowBundled=[]))
        config_path = directory / 'config.json'
        save(config_path, config)
        env = {k: v for k, v in os.environ.items() if not any(s in k for s in ('API_KEY', 'ACCESS_TOKEN', 'SECRET', 'AWS_', 'SUPABASE'))}
        env.update(OPENCLAW_STATE_DIR=str(state), OPENCLAW_CONFIG_PATH=str(config_path), OPENCLAW_SKIP_CHANNELS='1',
                   OPENCLAW_DISABLE_DISCOVERY='1', DO_NOT_TRACK='1')
        result = subprocess.run([str(NODE), str(CLAW), 'agent', 'exec', '--config', str(config_path), '--state-dir', str(state),
            '--cwd', str(workspace), '--message-file', str(message), '--model', 'lab/' + model.MODEL,
            '--code-mode', 'direct', '--thinking', 'off', '--timeout', '180', '--json'], capture_output=True, timeout=200, env=env)
        (directory / 'stdout.bin').write_bytes(result.stdout)
        (directory / 'stderr.bin').write_bytes(result.stderr)
        save(directory / 'exit.json', {'code': result.returncode})
        if result.returncode or self.logical_response is None:
            raise ValueError('OpenClaw did not complete its scoped model turn; retained CLI output.')
        envelope = json.loads(result.stdout)
        save(directory / 'envelope.json', envelope)
        # Validate the actual final text, not merely an answer captured by the bridge.
        payloads = envelope.get('payloads') or envelope.get('result', {}).get('payloads') or []
        texts = [p['text'] for p in payloads if p.get('text')]
        expected = model.parse(self.logical_response)
        if len(texts) != 1 or json.loads(texts[0]) != expected:
            raise ValueError('OpenClaw final answer differs from the retained provider decision')
        return self.logical_response


def translate(request, logical):
    """OpenClaw text-only transport -> identical fixed structured decision schema."""
    if request.get('model') != model.MODEL or request.get('tools'):
        raise ValueError('Unexpected model or agent tools in the lab-only transport')
    messages = []
    systems = []
    for item in request['messages']:
        content = item.get('content')
        if not isinstance(content, str):
            if not isinstance(content, list) or any(p.get('type') != 'text' for p in content):
                raise ValueError('Only text inputs are admitted')
            content = '\n'.join(p['text'] for p in content)
        if item['role'] in ('system', 'developer'):
            systems.append({'text': content})
        elif item['role'] in ('user', 'assistant'):
            messages.append(dict(role=item['role'], content=[dict(text=content)]))
        else:
            raise ValueError('Unexpected tool/history role')
    return dict(modelId=model.MODEL, system=systems, messages=messages,
                inferenceConfig=dict(maxTokens=4096, temperature=0), toolConfig=copy.deepcopy(logical['toolConfig']))


def server(provider):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            try:
                if self.path != '/v1/chat/completions' or self.headers.get('Authorization') != 'Bearer ' + provider.token:
                    self.send_error(403); return
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 96000:
                    self.send_error(413); return
                raw = self.rfile.read(length)
                request = json.loads(raw)
                if provider.smoke:
                    save(BASE / provider.arm / 'submitted-request.json', request)
                if provider.logical_response is not None:
                    raise ValueError('Duplicate model round prohibited')
                response = provider.call(translate(request, provider.logical), raw)
                provider.logical_response = response
                text = workflow.canonical(model.parse(response)).decode()
                usage = response.get('usage') or {}
                item = dict(id='chatcmpl-lab-pilot', object='chat.completion', created=int(time.time()), model=model.MODEL,
                    choices=[dict(index=0, message=dict(role='assistant', content=text), finish_reason='stop')],
                    usage=dict(prompt_tokens=usage.get('inputTokens'), completion_tokens=usage.get('outputTokens'), total_tokens=usage.get('totalTokens')))
                if request.get('stream'):
                    chunks = [dict(id=item['id'], object='chat.completion.chunk', created=item['created'], model=model.MODEL,
                        choices=[dict(index=0, delta=dict(role='assistant', content=text), finish_reason=None)]),
                        dict(id=item['id'], object='chat.completion.chunk', created=item['created'], model=model.MODEL,
                        choices=[dict(index=0, delta={}, finish_reason='stop')], usage=item['usage'])]
                    body = b''.join(b'data: ' + workflow.canonical(c) + b'\n\n' for c in chunks) + b'data: [DONE]\n\n'
                    kind = 'text/event-stream'
                else:
                    body = workflow.canonical(item); kind = 'application/json'
                self.send_response(200); self.send_header('Content-Type', kind); self.send_header('Content-Length', str(len(body))); self.end_headers()
                self.wfile.write(body)
            except Exception as error:
                provider.bridge_error = str(error)
                self.send_error(400, 'Pilot request refused; inspect retained local audit')
    provider.token = secrets.token_urlsafe(32)
    http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    provider.url = 'http://127.0.0.1:' + str(http.server_address[1])
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    return http, thread


def trajectory(provider, arm, packet):
    activate(arm, packet)
    provider.arm = arm['name']
    study, task_id = arm['study_id'], arm['task_id']
    report = dict(name=arm['name'], started_at=stamp(), controller_turns=[], status='running')
    began = time.monotonic()

    def invoke(request):
        provider.logical = request
        return provider.call(request) if arm['name'] == 'jumpserve-direct' else provider.openclaw(request)

    try:
        with patch.object(model, 'invoke', side_effect=invoke):
            for _ in range(120):
                if time.monotonic() >= provider.deadline:
                    report.update(status='limited', reason='Pilot deadline reached'); break
                database.query("update research_autonomy_tasks set next_at=clock_timestamp() where study_id=" + database.sql_text(study) + " and status='waiting'")
                task = database.rpc('research_autonomy_control', dict(p_operation='lease', p_actor=None, p_study=None, p_payload={}))
                if task:
                    if task['id'] != task_id:
                        raise ValueError('Unexpected claim identity')
                    before = task['attempts']
                    outcome = autonomy.process(task)
                    after = autonomy.one('autonomy_tasks', study, task_id)
                    if after['attempts'] > before:
                        report['controller_turns'].append(dict(number=after['attempts'], stage=task['stage'], outcome=outcome))
                        print(json.dumps(dict(arm=arm['name'], turn=after['attempts'], stage=task['stage'], status=after['status'])), flush=True)
                worker.handler({})
                current = autonomy.one('autonomy_tasks', study, task_id)
                if current['status'] in ('resolved', 'blocked', 'attempt-limit', 'budget-limit', 'deadline', 'budget'):
                    report.update(status='complete', task_status=current['status'], label=current['label'], reason=current['reason'])
                    if current['context'].get('assessment_id'):
                        report['assessment'] = autonomy.one('assessments', study, current['context']['assessment_id'])
                    break
            else:
                report.update(status='limited', reason='Frozen cycle limit reached')
    except Exception as error:
        report.update(status='failed', reason=str(error))
    finally:
        report.update(ended_at=stamp(), wall_seconds=time.monotonic()-began)
        for kind in ('autonomy_turns', 'autonomy_tasks', 'autonomy_workflows', 'queue_jobs', 'runs', 'measurements', 'published_values', 'summaries', 'assessments'):
            rows = autonomy.rows(kind, study, limit=1000)
            # measurements can exceed the normal page; collect complete recorded data.
            if kind in ('measurements', 'published_values'):
                rows = autonomy.evidence.collect(autonomy.rows, kind, study)
            save(BASE / arm['name'] / (kind + '.json'), rows)
        for path, raw in database.files.items():
            destination = BASE / arm['name'] / 'final-originals' / sha(raw)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
        save(BASE / arm['name'] / 'report.json', report)
    return report


def compare(packet, reports):
    normalized = []
    checks = {}
    for arm, report in zip(packet['arms'], reports):
        rows = json.loads((BASE / arm['name'] / 'measurements.json').read_bytes())
        values = sorted([(r['observation_id'], r['configuration_identity'], r['metric'], r['units'], r['status'],
            str(r['value']) if r['value'] is not None else None, r.get('reason')) for r in rows])
        normalized.append(values)
        oracle = True
        for row in rows:
            d = row['details']
            if row['status'] != 'recorded':
                oracle = False; continue
            with localcontext() as ctx:
                ctx.prec = 100
                difference = Decimal(str(row['value'])) - Decimal(d['published'])
                oracle &= difference == Decimal(d['difference']) and abs(difference) <= Decimal(d['tolerance_absolute'])
        coverage = report.get('assessment', {}).get('tested_conditions', {}).get('coverage', {})
        checks[arm['name']] = dict(resolved=report.get('task_status') == 'resolved', label=report.get('label') == 'reproduced',
            complete_cells=len(rows) == 1152 and coverage.get('covered_cells') == 1152, decimal_oracle=bool(oracle and rows))
    checks['matched_measurements_equal'] = normalized[0] == normalized[1] and len(normalized[0]) == 1152
    passed = checks['matched_measurements_equal'] and all(all(checks[name].values()) for name in ARM_NAMES)
    return dict(passed=passed, checks=checks, interpretation='One exposed archived claim, paired runtime feasibility only. Descriptive comparison, no inferential interval or general ranking.')


def verify_audit(provider, packet):
    pid = database.sql_text(packet['id'])
    records = json.loads(provider.query("select coalesce(json_agg(t),'[]') from (select arm,number,logical_request_sha256,request_sha256,encode(request_raw,'hex') request_hex,response_sha256,encode(response_raw,'hex') response_hex,transport_sha256,encode(transport_raw,'hex') transport_hex from research_runtime_pilot_calls where pilot_id=" + pid + ' order by arm,number)t'))
    checked = 0
    for arm in packet['arms']:
        turns = json.loads((BASE / arm['name'] / 'autonomy_turns.json').read_bytes())
        hashes = set()
        for turn in turns:
            if sha(turn['request_raw'].encode()) != turn['request_sha256']:
                raise ValueError('Controller prompt hash differs after database retrieval')
            hashes.add(turn['request_sha256'])
        for row in (r for r in records if r['arm'] == arm['name']):
            if row['logical_request_sha256'] not in hashes:
                raise ValueError('Actual provider call is not linked to a retained controller turn')
            directory = BASE / arm['name'] / ('provider-' + str(row['number']).zfill(2))
            for field, file in (('request', 'request.json'), ('response', 'response.bin'), ('transport', 'openclaw-http-body.bin')):
                original = row[field + '_hex']
                if original is None:
                    continue
                raw = bytes.fromhex(original)
                if sha(raw) != row[field + '_sha256'] or (directory / file).read_bytes() != raw:
                    raise ValueError('Stored original bytes differ after retrieval: ' + field)
                checked += 1
    save(BASE / 'database-original-byte-export.json', records)
    return dict(passed=True, provider_calls=len(records), byte_records_verified=checked,
                original_controller_prompt_links_verified=True, provider_http_response_wire_available=False)


def live():
    packet = json.loads((BASE / 'frozen.json').read_bytes())
    verify_frozen(packet)
    target = BASE / 'report.json'
    if target.exists():
        raise ValueError('Pilot already attempted; preserve all attempts and unknown outcomes.')
    spec = importlib.util.spec_from_file_location('pilot_target_guard', INFRA / 'bin/autonomy-release.py')
    release = importlib.util.module_from_spec(spec); spec.loader.exec_module(release)
    targets = release.guard.target()
    provider = Provider(packet)
    pid = database.sql_text(packet['id'])
    state = provider.query('select status from research_runtime_pilots where id=' + pid)
    if state != 'prepared':
        raise ValueError('Pilot was already admitted; no replay')
    provider.query("update research_runtime_pilots set status='running',started_at=clock_timestamp() where id=" + pid)
    report = dict(id=packet['id'], protocol_sha256=sha(PROTOCOL.read_bytes()), targets=targets, started_at=stamp(),
                  arms=[], passed=False, production_mutations=0, measured_charges_usd=None,
                  evaluator=dict(identity='Codex primary implementer', type='AI', independent=False), human_review=False)
    save(target, report)
    http, thread = server(provider)
    try:
        for arm in packet['arms']:
            report['arms'].append(trajectory(provider, arm, packet)); save(target, report)
        report.update(compare(packet, report['arms']))
        report['audit_verification'] = verify_audit(provider, packet)
    finally:
        http.shutdown(); thread.join(timeout=5); http.server_close()
        report['calls'] = json.loads(provider.query("select coalesce(json_agg(t),'[]') from (select arm,number,status,reserved_cents,estimated_cents,usage,request_sha256,response_sha256,started_at,ended_at from research_runtime_pilot_calls where pilot_id=" + pid + ' order by started_at)t'))
        report['prior_reserved_cents'] = packet['protocol'].get('prior_reserved_cents', 0)
        report['reserved_cents'] = 1000 + report['prior_reserved_cents'] + sum(c['reserved_cents'] for c in report['calls'])
        report['known_estimated_model_cents'] = sum(c['estimated_cents'] for c in report['calls'] if c['estimated_cents'] is not None)
        report['missing_usage_calls'] = sum(c['estimated_cents'] is None for c in report['calls'])
        report.update(ended_at=stamp(), review_status='AI implementer inspection pending; no independent/human review')
        save(target, report)
        provider.query("update research_runtime_pilots set status=" + database.sql_text('complete' if report['passed'] else 'failed') +
            ',ended_at=clock_timestamp(),report=' + database.sql_json(report) + ' where id=' + pid)
        print(json.dumps(dict(report=str(target), passed=report['passed'], calls=len(report['calls']), reserved_cents=report['reserved_cents'])))


def smoke():
    """Actual OpenClaw CLI and HTTP bridge, explicitly synthetic model answer."""
    provider = Provider(dict(protocol=load_protocol()), smoke=True)
    provider.arm = 'smoke-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    logical = model.request('Return one structured decision. This is a synthetic transport smoke check, not scientific evidence.',
                            {'task': 'planning', 'remaining_commitment_cents': 1000})
    http, thread = server(provider)
    try:
        response = provider.openclaw(logical)
        save(BASE / provider.arm / 'report.json', dict(passed=model.parse(response)['action'] == 'blocked',
            actual_openclaw=True, actual_model_calls=0, synthetic_provider=True, requests=len(provider.smoke_requests)))
        print(json.dumps(dict(directory=str(BASE / provider.arm), passed=True, actual_model_calls=0)))
    finally:
        save(BASE / provider.arm / 'transport-diagnostic.json', dict(bridge_error=getattr(provider, 'bridge_error', None),
             synthetic_requests=len(provider.smoke_requests), actual_model_calls=0))
        http.shutdown(); thread.join(timeout=5); http.server_close()


def check():
    """Actual local SQL controls and archived arithmetic, zero provider calls."""
    began = time.monotonic()
    directory = ROOT / 'artifacts/development' / dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    report = dict(started_at=stamp(), passed=False, model_calls=0, checks={}, source_hashes={}, measured_charges_usd=None)
    try:
        database.bootstrap()
        database.query(MIGRATION.read_text())
        database.rpc('publish_agent_prompt', autonomy_fixtures.publish_fixture())
        pid = str(uuid.uuid4()); protocol_raw = PROTOCOL.read_bytes()
        database.query("insert into research_runtime_pilots(id,protocol_raw,protocol_sha256,configuration,status,maximum_cents,overhead_cents) values(" +
            database.sql_text(pid) + ",decode('" + protocol_raw.hex() + "','hex')," + database.sql_text(sha(protocol_raw)) + ",'{}','prepared',10000,1000)")
        database.rejects(lambda: database.query("update research_runtime_pilots set protocol_sha256='" + '0'*64 + "' where id=" + database.sql_text(pid)))
        report['checks']['frozen_protocol_immutable'] = True
        for role in ('anon', 'authenticated'):
            for name in ('research_runtime_pilots', 'research_runtime_pilot_calls'):
                if database.query("select has_table_privilege(current_user," + database.sql_text(name) + ",'SELECT,INSERT,UPDATE,DELETE')", role=role) != 'f':
                    raise ValueError('Browser access to private pilot traces')
        report['checks']['browser_access_denied'] = True
        request = workflow.canonical(dict(value=None, recorded_zero=0)); response = b'{"synthetic":true}'
        cid = str(uuid.uuid4())
        database.query("insert into research_runtime_pilot_calls(id,pilot_id,arm,number,logical_request_sha256,request_raw,request_sha256,status,reserved_cents) values(" +
            ','.join([database.sql_text(cid), database.sql_text(pid), "'jumpserve-direct'", '1', database.sql_text(sha(request)),
                      "decode('" + request.hex() + "','hex')", database.sql_text(sha(request)), "'running'", '1']) + ')')
        database.rejects(lambda: database.query("update research_runtime_pilot_calls set reserved_cents=0 where id=" + database.sql_text(cid)))
        database.query("update research_runtime_pilot_calls set response_raw=decode('" + response.hex() + "','hex'),response_sha256=" +
                       database.sql_text(sha(response)) + ",status='complete' where id=" + database.sql_text(cid))
        database.rejects(lambda: database.query("update research_runtime_pilot_calls set response_raw=decode('7b7d','hex') where id=" + database.sql_text(cid)))
        stored = json.loads(database.query("select json_build_object('request',encode(request_raw,'hex'),'response',encode(response_raw,'hex')) from research_runtime_pilot_calls where id=" + database.sql_text(cid)))
        if bytes.fromhex(stored['request']) != request or bytes.fromhex(stored['response']) != response:
            raise ValueError('Stored original bytes differ after retrieval')
        report['checks']['original_call_bytes_immutable_and_verified'] = True
        original = BACKEND / 'research_workflow/plans/ipv6-archive-v1.json'
        raw = original.read_bytes(); plan = json.loads(raw)
        compiled = preparation.compile_plan(plan, raw, dict(id=str(uuid.uuid4()), paper_url=plan['paper_urls'][0]))
        records = {}
        for e in compiled['records']:
            records.setdefault(e['kind'], {})[e['record']['id']] = e['record']
        total = 0
        for job in (j for j in compiled['jobs'] if j['execution_mode'] == 'automatic'):
            campaign = records['campaigns'][job['campaign_id']]
            result = runner.execute(job['request']['raw_input'].encode(), records['protocols'][campaign['protocol_id']], campaign, job['id'])
            if result['run']['status'] != 'complete' or any(r['details']['label'] != 'reproduced' for r in result['measurements']):
                raise ValueError('Original archived arithmetic preflight differs')
            total += len(result['measurements'])
        report['checks']['six_archived_checks_1152_cells'] = total == 1152
        report['source_hashes'] = database.report['migration_sha256']
        report['passed'] = all(report['checks'].values())
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        report.update(ended_at=stamp(), wall_seconds=time.monotonic()-began, database=database.DATABASE)
        save(directory / 'report.json', report)
        print(json.dumps(dict(report=str(directory / 'report.json'), passed=report['passed'], model_calls=0)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['prepare', 'run', 'smoke', 'check'])
    args = parser.parse_args()
    {'prepare': prepare, 'run': live, 'smoke': smoke, 'check': check}[args.operation]()
