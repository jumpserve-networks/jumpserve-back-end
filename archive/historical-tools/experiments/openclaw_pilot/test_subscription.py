"""Subscription admission and stream regressions. Synthetic providers only."""
import copy
import io
import json
import sqlite3
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import subscription as sub


def decision():
    return dict(action='blocked', reason='missing-required-inputs', blockers=[dict(kind='missing-data',
        detail='missing-source-data', required_input='Synthetic fixture has no source data.')], claims=[],
        check_id=None, proposal=None, ready=None, coverage_complete=False, assessment_label=None, evidence_ids=[])


def completed(observed=None):
    value = dict(model=sub.MODEL, status='completed', output=[dict(type='message', role='assistant',
        content=[dict(type='output_text', text=json.dumps(decision()))])])
    if observed is not None:
        value['usage'] = observed
    return value


def sse(*events):
    return b''.join(b'data: ' + json.dumps(event).encode() + b'\n\n' for event in events)


class Response(io.BytesIO):
    def __init__(self, raw, status=200):
        super().__init__(raw)
        self.status = status
        self.headers = {'x-request-id': 'synthetic-request'}


class Ledger:
    def __init__(self):
        self.admitted = False
        self.finished = None

    def __call__(self, sql):
        if sql.startswith('select json_build_object'):
            return json.dumps(dict(count=int(self.admitted), reserved=100 if self.admitted else 0,
                                   unsafe=int(self.admitted)))
        if sql.startswith('insert into research_subscription_calls'):
            self.admitted = True
        if sql.startswith('update research_subscription_calls'):
            self.finished = sql
        return ''


class Subscription(unittest.TestCase):
    def setUp(self):
        self.patches = sub.configuration(); self.patches.__enter__()
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.base_patch = patch.object(sub, 'BASE', self.base); self.base_patch.start()
        self.lab_patch = patch.object(sub.lab, 'BASE', self.base); self.lab_patch.start()
        self.packet = dict(id=str(uuid.uuid4()), coordinator_database='local-synthetic', prior_committed_cents=2715,
                           protocol=sub.protocol())
        self.logical = sub.lab.model.request('Synthetic software test; no scientific evidence.',
                                           dict(task='planning', missing=None, recorded_zero=0))

    def tearDown(self):
        self.lab_patch.stop(); self.base_patch.stop(); self.temporary.cleanup()
        self.patches.__exit__(None, None, None)

    def provider(self, response):
        self.dispatched = []
        def transport(method, url, token, body):
            self.dispatched.append((method, url, token, body))
            return response
        value = sub.Provider(self.packet, token_source=lambda: 'synthetic-subscription-token', transport=transport)
        value.logical = self.logical; value.arm = 'openclaw-runtime'; value.query = Ledger()
        return value

    def test_request_preserves_missing_zero_schema_and_removes_unsupported_parameters(self):
        before = copy.deepcopy(self.logical)
        request = sub.response_request(self.logical)
        self.assertEqual(self.logical, before)
        self.assertEqual(request['model'], sub.MODEL)
        self.assertEqual(request['tools'], [])
        self.assertFalse(request['store']); self.assertTrue(request['stream'])
        self.assertFalse({'temperature', 'max_output_tokens', 'previous_response_id', 'background'} & set(request))
        context = json.loads(request['input'][0]['content'])
        self.assertIsNone(context['missing']); self.assertEqual(context['recorded_zero'], 0)
        self.assertIn('assessment_label', request['instructions'])

    def test_only_explicit_unexpired_subscription_permission_is_accepted(self):
        value = dict(type='oauth', provider='openai', authFlow='chatgpt-token-sharing', issuer='https://auth.openai.com',
                     grantedScope='resource.invoke chatgpt.tokens.use.direct', access='synthetic-test-access', expires=200000)
        self.assertEqual(sub.validate_credential(value, now=100), value['access'])
        for field, changed in (('type','api_key'),('access','sk-synthetic'),('authFlow','chatgpt-identity'),
                               ('grantedScope','openid'),('expires',100000),('provider','other')):
            with self.assertRaises(ValueError):
                sub.validate_credential(dict(value, **{field:changed}), now=100)

    def test_environment_does_not_inherit_paid_credentials_or_endpoint_overrides(self):
        with patch.dict(sub.os.environ, {'OPENAI_API_KEY':'synthetic', 'ANTHROPIC_API_KEY':'synthetic',
             'AWS_PROFILE':'synthetic', 'OPENAI_BASE_URL':'https://invalid.example', 'HTTPS_PROXY':'https://invalid.example'}):
            actual = sub.environment()
        self.assertFalse({'OPENAI_API_KEY','ANTHROPIC_API_KEY','AWS_PROFILE','OPENAI_BASE_URL','HTTPS_PROXY'} & set(actual))

    def test_frozen_ca_keeps_tls_and_hostname_verification_enabled(self):
        context=sub.tls_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode,sub.ssl.CERT_REQUIRED)
        self.assertIsNone(context.keylog_filename)

    def test_exact_native_shared_auth_store_and_no_alternate_profile_fallback(self):
        root=self.base / 'private-auth';(root / 'state').mkdir(parents=True)
        value=dict(type='oauth',provider='openai',authFlow='chatgpt-token-sharing',issuer='https://auth.openai.com',
            grantedScope='resource.invoke chatgpt.tokens.use.direct',access='synthetic-access',expires=(time.time()+3600)*1000)
        with sqlite3.connect(root / 'state/openclaw.sqlite') as db:
            db.execute('create table config_machine_state(state_key text primary key,value_json text)')
            db.execute('insert into config_machine_state values(?,?)',('authProfiles.store',json.dumps(dict(version=1,profiles={'synthetic':value}))))
        agent=root / 'agents/main/agent';agent.mkdir(parents=True)
        with sqlite3.connect(agent / 'openclaw-agent.sqlite') as db:
            db.execute('create table auth_profile_store(store_key text primary key,store_json text)')
        with patch.object(sub,'AUTH',root):
            self.assertEqual(sub.credential(),'synthetic-access')
            with sqlite3.connect(agent / 'openclaw-agent.sqlite') as db:
                db.execute('insert into auth_profile_store values(?,?)',('primary',json.dumps(dict(profiles={'other':{'type':'api_key','provider':'openai'}}))))
            with self.assertRaises(ValueError):sub.credential()

    def test_completed_stream_preserves_raw_bytes_and_zero_is_not_missing(self):
        raw = sse(dict(type='response.output_text.delta', delta='partial'),
                  dict(type='response.completed', response=completed(dict(input_tokens=0,output_tokens=0,total_tokens=0))))
        sink = io.BytesIO()
        value = sub.read_stream(Response(raw), sink, time.monotonic()+10, 1048576)
        self.assertEqual(sink.getvalue(), raw)
        self.assertEqual(sub.usage(value), dict(inputTokens=0,outputTokens=0,totalTokens=0))
        self.assertIsNone(sub.usage(completed()))
        self.assertEqual(sub.usage(completed(dict(input_tokens=True))), dict(inputTokens=None,outputTokens=None,totalTokens=None))

    def test_quota_after_partial_stream_pauses_and_retains_original_error(self):
        raw = sse(dict(type='response.output_text.delta', delta='do not treat this as success'),
                  dict(type='response.failed', response=dict(error=dict(code='subscription_sharing_usage_limit_exceeded'))))
        provider = self.provider(Response(raw))
        with self.assertRaisesRegex(ValueError, 'usage_limit_exceeded'):
            provider.call(self.logical, b'{"synthetic":true}')
        self.assertEqual(provider.stop_status, 'paused-quota')
        self.assertEqual((self.base / 'openclaw-runtime/provider-01/response.bin').read_bytes(), raw)
        with self.assertRaises(ValueError):
            provider.call(self.logical, b'{"synthetic":true}')
        self.assertEqual(len(self.dispatched), 1)

    def test_http_quota_unknown_stream_and_invalid_decision_prevent_further_dispatch(self):
        cases = [Response(b'{"error":{"code":"subscription_sharing_usage_limit_exceeded"}}',429),
                 Response(sse(dict(type='response.output_text.delta',delta='{}'))),
                 Response(b'data: malformed\n\n')]
        for index, response in enumerate(cases):
            with self.subTest(index=index), patch.object(sub, 'BASE', self.base / str(index)):
                provider = self.provider(response)
                with self.assertRaises(ValueError): provider.call(self.logical,b'{}')
                with self.assertRaises(ValueError): provider.call(self.logical,b'{}')
                self.assertEqual(len(self.dispatched),1)
        value = completed(); value['output'][0]['content'][0]['text']='{"assessment_label":"reproduced"}'
        with self.assertRaises(ValueError): sub.structured_response(value)
        value = completed(); value['output']=[dict(type='function_call',name='exec')]
        with self.assertRaises(ValueError): sub.structured_response(value)

    def test_limits_and_invalid_credentials_fail_before_network(self):
        for field, value in (('prior_committed_cents',10000),):
            provider = self.provider(Response(b'')); provider.packet=copy.deepcopy(self.packet);provider.packet[field]=value
            with self.assertRaises(ValueError): provider.call(self.logical,b'{}')
            self.assertFalse(self.dispatched)
        provider = self.provider(Response(b''))
        with self.assertRaises(ValueError): provider.call(self.logical,b'x'*96001)
        self.assertFalse(self.dispatched)
        provider = self.provider(Response(b''))
        provider.query=lambda sql:json.dumps(dict(count=25,reserved=2500,unsafe=0))
        with self.assertRaises(ValueError):provider.call(self.logical,b'{}')
        self.assertFalse(self.dispatched)
        provider = self.provider(Response(b''))
        provider.token_source=lambda: sub.validate_credential({'type':'api_key'})
        with self.assertRaises(ValueError): provider.call(self.logical,b'{}')
        self.assertFalse(self.dispatched)

    def test_paused_offline_status_reports_partial_usage_and_unknown_subscription_cost(self):
        sub.lab.save(self.base / 'report.json',dict(passed=False,pause_status='paused-quota',calls=[
            dict(usage=dict(inputTokens=0,outputTokens=3,totalTokens=3)),dict(usage=None)],
            aggregate_committed_cents=3915,covered_cells=0))
        with patch.object(sub,'http_request') as network:
            value=sub.status();network.assert_not_called()
        self.assertEqual(value['status'],'paused-quota')
        self.assertEqual(value['usage']['inputTokens'],dict(known_sum=0,missing_calls=1))
        self.assertIsNone(value['subscription_allocation_usd']);self.assertIsNone(value['allowance_remaining'])
        self.assertFalse(value['paid_api_fallback'])

    def test_unconfirmed_transport_does_not_invent_an_empty_provider_response(self):
        provider=self.provider(Response(b''))
        def disconnected(*args):raise ConnectionError('Synthetic dropped connection')
        provider.transport=disconnected
        with self.assertRaises(ValueError):provider.call(self.logical,b'{}')
        self.assertEqual(provider.stop_status,'unknown')
        self.assertIn('response_raw=null,response_sha256=null',provider.query.finished)
        receipt=json.loads((self.base / 'openclaw-runtime/provider-01/receipt.json').read_bytes())
        self.assertFalse(receipt['response_received']);self.assertIsNone(receipt['response_sha256'])

    def test_real_openclaw_cli_against_synthetic_subscription_bridge(self):
        raw = sse(dict(type='response.completed',response=completed(dict(input_tokens=100,output_tokens=30,total_tokens=130))))
        provider=self.provider(Response(raw))
        http,thread=sub.lab.server(provider)
        try:
            response=provider.openclaw(self.logical)
            self.assertEqual(sub.lab.model.parse(response)['action'],'blocked')
            self.assertEqual(len(self.dispatched),1)
            actual=json.loads(self.dispatched[0][3])
            self.assertEqual(actual['model'],sub.MODEL)
            self.assertFalse(actual['store']);self.assertTrue(actual['stream']);self.assertEqual(actual['tools'],[])
            self.assertIn('decision schema',actual['instructions'])
        finally:
            http.shutdown();thread.join(timeout=5);http.server_close()


class SubscriptionDatabase(unittest.TestCase):
    def test_real_postgres_protocol_terminal_bytes_cost_missingness_and_browser_denial(self):
        f=sub.lab.database
        f.DATABASE='jumpserve_research_ideas_test_subscription_controls_'+uuid.uuid4().hex
        f.query.__defaults__=(f.DATABASE,None)
        f.bootstrap();f.query(sub.MIGRATION.read_text())
        # Original core prompt evidence keeps its original model identity.
        f.rpc('publish_agent_prompt',sub.lab.autonomy_fixtures.publish_fixture())
        with sub.configuration():
            _,system=sub.lab.model.load_prompt()
            logical=sub.lab.model.request(system,dict(task='planning'))
            self.assertEqual(logical['modelId'],sub.MODEL)
        study=str(uuid.uuid4());pid=str(uuid.uuid4());cid=str(uuid.uuid4())
        f.rpc('research_create_study',dict(p_actor=f.ACTOR,p_id=study,p_document=dict(title='Synthetic controls',
              paper_url='https://example.org/synthetic.pdf',domain='Software',scope='Not scientific evidence',study_kind='paper')))
        raw=b'{"missing":null,"zero":0}'
        f.query('insert into research_subscription_pilots(id,study_id,protocol_raw,protocol_sha256,configuration,prior_committed_cents,overhead_cents,maximum_cents,status) values('+','.join([
            f.sql_text(pid),f.sql_text(study),sub.sql_bytes(raw),f.sql_text(sub.lab.sha(raw)),"'{}'",'2715','1000','10000',"'prepared'"])+')')
        f.query('insert into research_subscription_calls(id,pilot_id,number,logical_raw,logical_sha256,request_raw,request_sha256,transport_raw,transport_sha256,status,reserved_cents) values('+','.join([
            f.sql_text(cid),f.sql_text(pid),'1',sub.sql_bytes(raw),f.sql_text(sub.lab.sha(raw)),sub.sql_bytes(raw),f.sql_text(sub.lab.sha(raw)),sub.sql_bytes(raw),f.sql_text(sub.lab.sha(raw)),"'running'",'100'])+')')
        f.rejects(lambda:f.query("update research_subscription_pilots set configuration='{}'::jsonb || '{\"paid_fallback\":true}'::jsonb where id="+f.sql_text(pid)))
        f.rejects(lambda:f.query('update research_subscription_calls set estimated_cents=0 where id='+f.sql_text(cid)))
        f.rejects(lambda:f.query('update research_subscription_calls set reserved_cents=0 where id='+f.sql_text(cid)))
        f.query("update research_subscription_calls set status='paused-quota',response_raw="+sub.sql_bytes(raw)+',response_sha256='+f.sql_text(sub.lab.sha(raw))+' where id='+f.sql_text(cid))
        stored=json.loads(f.query("select json_build_object('raw',encode(response_raw,'hex'),'hash',response_sha256,'usage',usage,'estimated',estimated_cents) from research_subscription_calls where id="+f.sql_text(cid)))
        self.assertEqual(bytes.fromhex(stored['raw']),raw);self.assertEqual(stored['hash'],sub.lab.sha(raw))
        self.assertIsNone(stored['usage']);self.assertIsNone(stored['estimated'])
        f.rejects(lambda:f.query("update research_subscription_calls set status='complete' where id="+f.sql_text(cid)))
        f.rejects(lambda:f.query('delete from research_subscription_calls where id='+f.sql_text(cid)))
        for role in ('anon','authenticated'):
            for table in ('research_subscription_pilots','research_subscription_calls'):
                self.assertEqual(f.query('select has_table_privilege(current_user,'+f.sql_text(table)+",'SELECT,INSERT,UPDATE,DELETE')",role=role),'f')


if __name__=='__main__':
    unittest.main()
