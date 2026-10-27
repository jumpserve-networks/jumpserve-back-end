"""Fixed loopback-only HTTP fixture: actual handler/projection, synthetic store.

No credentials, model calls, real database reads or experiment execution.
Used only by the public queue browser verification entry point.
"""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_public_queue import fixture, STUDY, TASK, CLAIM, JOB, STAMP, SECRET
from test_paper_titles import uploaded_study, packet, artifact
from test_paper_catalog import fixture as paper_fixture
import api
import autonomy
import paper_titles
import store
from workflow import PUBLIC_KINDS, canonical,digest

mode = 'normal'
title_packet = canonical(packet())
NATIVE_CALL='70000000-0000-4000-8000-000000000001'
NATIVE_REQUEST='70000000-0000-4000-8000-000000000002'
NATIVE_RESPONSE='70000000-0000-4000-8000-000000000003'
native_request=canonical(dict(model='gpt-6.1-sol',input=[dict(role='user',content='Write and test a bounded experiment.')]))
native_response=b'data: {"type":"response.completed","response":{"output":[{"type":"function_call","name":"exec","arguments":"{\\"command\\":\\"python3 experiment.py\\"}"}]}}\n\n'
def artifact_bytes(study,identity,**kwargs):return native_request if identity==NATIVE_REQUEST else native_response if identity==NATIVE_RESPONSE else title_packet
def data():
    value = fixture()
    value['autonomy_workflows'][0]['reason'] = 'The assessment is waiting for archived inputs.'
    value['autonomy_workflows'][0].update(initial_prompt='Assess only source-grounded networking claims. API_KEY=PRIVATE-CANARY-key', initial_sha256='a'*64)
    value['autonomy_tasks'][0].update(reason='ValueError: archived input version is unavailable. API_KEY=PRIVATE-CANARY-secret-key',
        required_inputs=['Supply the archived 1988 trace in CSV format.', SECRET], attempts=12)
    value['autonomy_turns'] = [dict(id=f'60000000-0000-4000-8000-{i:012}', study_id=STUDY, task_id=TASK, number=i, stage='planning',
        prompt_version_id=CLAIM, model_id='fixed-fixture-model', request=dict(system=[dict(text='Check the archived configuration in milliseconds.')], headers=dict(Authorization='Bearer PRIVATE-CANARY-token')),
        request_sha256='b'*64, status='failed', reserved_cents=0, estimated_cents=None, raw_response=None, response_sha256=None, output=None,
        usage=dict(input_tokens=0, measured_charges_usd=None), reason='Input archive unavailable.', created_at=STAMP, ended_at=None) for i in range(1,13)]
    value['queue_jobs'][0]['reason'] = 'Executor timed out after 10 seconds. Input: /tmp/PRIVATE-CANARY-input.csv'
    value['runs'][0]['reason'] = 'Only 2 of 3 observations were recorded. Contact private-owner@example.org.'
    value['studies'][0] = dict(uploaded_study(), created_at='2026-10-22T00:00:01+00:00')
    extra = paper_fixture()[:12]
    extra[2] = dict(id=extra[2]['id'], created_at=extra[2]['created_at'], study_kind='paper',
        title='Synthetic published paper assessment', paper_url='https://example.org/paper.pdf',
        domain='Networking', scope='Synthetic software fixture; no scientific validation.', origin_module=None, paper_upload=None)
    value['studies'].extend(extra)
    value['studies'].append(dict(extra[0], id='dddddddd-1111-4111-8111-111111111111', study_kind='idea', title=SECRET))
    for paper, status in zip(extra, ('budget', 'cancelled', 'completed', 'completed-with-blockers', 'deadline')):
        value['autonomy_workflows'].append(dict(value['autonomy_workflows'][0], study_id=paper['id'], status=status))
    value['artifacts'] = [] if mode == 'title-missing' else [artifact(title_packet)]
    value['paper_reviews'] = []
    value['audit_events'] = [dict(id=1, study_id=STUDY, actor_id='99999999-2222-4222-8222-222222222222', action='intake', details=SECRET)]
    value['profiles'] = [dict(id='99999999-2222-4222-8222-222222222222', first_name='Casey', last_name='Researcher', email=SECRET, timezone=SECRET)]
    if mode == 'review-title':
        value['studies'][0].update(title='TCP88.pdf', paper_upload=dict(filename='TCP88.pdf', sha256='a'*64))
        value['paper_reviews'] = [dict(study_id=STUDY, status='confirmed', title='Congestion Avoidance and Control',
            authors=dict(version='paper-authors-v1',names=['Van Jacobson','Michael J. Karels'],page=1,method=SECRET),metadata=SECRET, confirmation=SECRET)]
        value['artifacts'] = []
    for index, label in enumerate(('reproduced', 'discrepant', 'inconclusive'), 7):
        claim = str(index) * 8 + '-1111-4111-8111-111111111111'
        task = str(index) * 8 + '-2222-4222-8222-222222222222'
        value['claims'].append(dict(id=claim, study_id=STUDY, description='Synthetic ' + label + ' candidate'))
        value['autonomy_tasks'].append(dict(value['autonomy_tasks'][0], id=task, claim_id=claim,
            label=label, status='blocked' if label == 'inconclusive' else 'resolved', stage='assessment', attempts=3))
    value['autonomy_tasks'].insert(0, dict(value['autonomy_tasks'][0], id='aaaaaaaa-1111-4111-8111-111111111111',
        claim_id=None, stage='intake', status='queued', label=None, attempts=0))
    value['queue_jobs'].append(dict(value['queue_jobs'][0], id='bbbbbbbb-1111-4111-8111-111111111111',
        run_id=None, status='queued'))
    for kind in PUBLIC_KINDS: value.setdefault(kind, [])
    value['configurations'] = [dict(id='a0000000-0000-4000-8000-000000000001',study_id=STUDY,created_at=STAMP,identity='explicit-software-fixture',details=dict(delay_ms=10,recorded_zero=0,missing=None,api_key='PRIVATE-CANARY-config-key'),input_versions=[],requested_resources={})]
    value['queue_events'] = [dict(id='a0000000-0000-4000-8000-000000000002',study_id=STUDY,job_id=JOB,event='failed',status='failed',reason='Fixture executor timed out.',details=dict(actor_id='PRIVATE-CANARY-actor',lease_token='PRIVATE-CANARY-lease'),created_at=STAMP)]
    published = extra[2]['id']
    public_claim = dict(id='cccccccc-1111-4111-8111-111111111111', study_id=published, created_at=STAMP,
        description='Synthetic published claim', location='Software fixture', claim_type='numerical')
    value['claims'].append(public_claim)
    value['publications'].append(dict(id='eeeeeeee-1111-4111-8111-111111111111', study_id=published,
        created_at=STAMP, version=1, status='published', record_ids={kind: [public_claim['id']] if kind == 'claims' else [] for kind in PUBLIC_KINDS}))
    if mode == 'empty':
        value['queue_jobs'] = []
        value['autonomy_tasks'] = value['autonomy_tasks'][:1]
    if mode == 'running': value['queue_jobs'][1]['status'] = 'running'
    if mode == 'details-missing':
        value['queue_jobs'][0]['reason'] = None; value['runs'][0]['reason'] = None
        value['autonomy_tasks'][1].update(reason=None, required_inputs=[])
    if mode == 'turns-missing': value['autonomy_turns'] = []
    value['builder_calls']=[]
    if mode.startswith('builder'):
        next(t for t in value['autonomy_tasks'] if t['id']==TASK).update(stage='building',status='running',attempts=31,reason='Building a versioned experiment from retained files.')
        value['builder_calls']=[dict(id=NATIVE_CALL,study_id=STUDY,task_id=TASK,dispatch_id=value['autonomy_turns'][-1]['id'],number=31,prompt_version_id=CLAIM,model_id='gpt-6.1-sol',request_artifact_id=NATIVE_REQUEST,request_sha256=digest(native_request),request_bytes=len(native_request),status='unknown' if mode=='builder-missing' else 'complete',reserved_cents=0,response_artifact_id=None if mode=='builder-missing' else NATIVE_RESPONSE,response_sha256=None if mode=='builder-missing' else digest(native_response),usage=None,reason=None,created_at=STAMP,ended_at=STAMP)]
        if mode=='builder-invalid':value['builder_calls'][0]['request_bytes']=0
    if mode == 'catalog-empty': value['studies'] = []
    return value

def rows(kind, params, public=False):
    value = data()[kind]
    if kind == 'profiles':
        if public: raise AssertionError('Profiles must remain private.')
        ids=params['id'][4:-1].split(',');value=[r for r in value if r['id'] in ids]
    elif kind == 'studies':
        if public:
            value = [p for p in value if p['id'] == paper_fixture()[2]['id']]
            if params.get('select') == 'id': value = [dict(id=p['id']) for p in value]
        if params.get('study_kind'): value = [p for p in value if p.get('study_kind') == 'paper']
        if params.get('id', '').startswith('eq.'): value = [p for p in value if p['id'] == params['id'][3:]]
        if 'or' in params:
            boundary = params['or']; stamp=boundary.split('created_at.lt.')[1].split(',')[0]; identity=boundary.split('id.lt.')[1].split(')')[0]
            from paper_catalog import timestamp
            value = [p for p in value if (timestamp(p['created_at']),p['id']) < (timestamp(stamp),identity)]
    elif params.get('study_id', '').startswith('eq.'):
        value = [r for r in value if r['study_id'] == params['study_id'][3:]]
    elif params.get('study_id', '').startswith('in.'):
        ids=params['study_id'][4:-1].split(',');value=[r for r in value if r['study_id'] in ids]
    else: raise AssertionError('Fixture requires explicit study scope.')
    if params.get('id', '').startswith('in.'):
        ids = params['id'][4:-1].split(','); value = [r for r in value if r['id'] in ids]
    if params.get('id', '').startswith('eq.'): value = [r for r in value if r['id'] == params['id'][3:]]
    if params.get('id', '').startswith('gt.'): value = [r for r in value if r['id'] > params['id'][3:]]
    if params.get('task_id', '').startswith('eq.'): value = [r for r in value if r['task_id'] == params['task_id'][3:]]
    if params.get('number', '').startswith('lt.'): value = [r for r in value if r['number'] < int(params['number'][3:])]
    if params.get('order') in ('id.asc','study_id.asc','number.desc'):
        key,direction=params['order'].split('.');value.sort(key=lambda r:r[key],reverse=direction=='desc')
    offset = params.get('offset', 0)
    return copy.deepcopy(value[offset:offset + params.get('limit', 200)])

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def reply(self, status, value):
        raw = json.dumps(value).encode()
        self.send_response(status); self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw))); self.send_header('Cache-Control', 'no-store')
        self.end_headers(); self.wfile.write(raw)
    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path == '/fixture/health': return self.reply(200, dict(fixture='public-queue-synthetic-store-v1', study=STUDY))
        if mode == 'unavailable': return self.reply(503, dict(error='Explicit software transport failure.'))
        response = api.handler(dict(rawPath=parsed.path, queryStringParameters={k: v[-1] for k, v in parse_qs(parsed.query).items()},
            headers={k.lower(): v for k, v in self.headers.items()}, requestContext={'http': {'method': 'GET'}}))
        result = json.loads(response['body'])
        if mode == 'invalid' and response['statusCode'] == 200: result['coverage']['jobs'] += 1
        if mode == 'catalog-invalid' and response['statusCode'] == 200 and parsed.path == '/research/papers': result['coverage']['papers'] += 1
        self.reply(response['statusCode'], result)
    def do_POST(self):
        global mode
        if self.path != '/fixture/mode': return self.reply(405, dict(error='Fixture does not accept research writes.'))
        request = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
        if request.get('mode') not in ('normal', 'empty', 'running', 'invalid', 'unavailable', 'title-missing', 'catalog-empty', 'catalog-invalid', 'review-title', 'details-missing', 'turns-missing','builder','builder-missing','builder-invalid'): return self.reply(400, dict(error='Fixed fixture states only.'))
        os.environ['RESEARCH_BUILDER_ENABLED']='true' if request['mode'].startswith('builder') else 'false'
        mode = request['mode']; paper_titles.retained_title.cache_clear(); self.reply(200, dict(mode=mode))

if __name__ == '__main__':
    if len(sys.argv) != 1: raise SystemExit('Fixed fixture only; no external address or commands.')
    with patch.object(store, 'rows', side_effect=rows), patch.object(store, 'artifact_bytes', side_effect=artifact_bytes), patch.object(autonomy, 'runtime_health', return_value=dict(
        runtime='openclaw-production-v1', model_id='explicit-software-fixture', status='ready')):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        print(json.dumps(dict(fixture='public-queue-synthetic-store-v1', port=server.server_port, study=STUDY)), flush=True)
        server.serve_forever()
