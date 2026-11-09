"""API ownership, immutable scientific inputs and error boundaries."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import urllib.error
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import api
import store
from workflow import canonical, digest
import test_network_execution as networking
import test_workflow as numerical

def uid(): return str(uuid.uuid4())


class ApiBoundaries(unittest.TestCase):
    def setUp(self):
        self.study, self.actor = uid(), uid(); self.path = '/research/studies/' + self.study
        self.enterContext(patch.dict(os.environ, {'SUPABASE_URL': 'https://' + store.PROJECT + '.supabase.co', 'SUPABASE_ANON_KEY': 'synthetic-public-key'}, clear=True))
        self.owns = self.enterContext(patch.object(store, 'owns', return_value=True))
    def event(self, path=None, method='GET', body=None, query=None):
        return dict(rawPath=path or self.path, requestContext={'http': {'method': method}}, body=json.dumps(body) if body is not None else None, queryStringParameters=query or {}, headers={})
    def call(self, suffix, method='POST', body=None, query=None): return api.dispatch(self.event(self.path + '/' + suffix, method, body, query), self.actor)
    def assert_http(self, status, operation):
        with self.assertRaises(api.HttpError) as error: operation()
        self.assertEqual(error.exception.status, status); return error.exception

    def test_router_rejects_unsupported_suffixes_missing_actors_and_excessive_owner_lists(self):
        for path in ['/research/prompts/version/extra','/research/papers/'+self.study+'/not-title']:
            self.assert_http(404,lambda path=path:api.dispatch(self.event(path),self.actor))
        self.assert_http(401,lambda:api.dispatch(self.event('/research/studies',method='POST',body={}),None))
        self.assert_http(404,lambda:api.dispatch(self.event('/research/studies',method='PUT'),self.actor))
        self.assert_http(404,lambda:self.call('unrecognized',method='PUT'))
        self.assert_http(401,lambda:api.dispatch(self.event(self.path+'/unrecognized',method='POST',body={}),None))
        with patch.object(store,'rows',return_value=[{'study_id':self.study}]*101):self.assert_http(413,lambda:api.dispatch(self.event('/research/studies',query={'mine':'1'}),self.actor))
        with self.assertRaisesRegex(ValueError,'raw_input text'):api.campaign_run(self.study,self.actor,{'request_id':uid(),'campaign_id':uid(),'raw_input':None})

    def test_autonomous_intake_and_retained_status_routes_delegate_exact_owner_scope(self):
        body={'autonomous':True,'request_id':uid(),'paper':{'title':'Original'}}
        with patch.object(api.autonomy,'start',return_value={'id':body['request_id']}) as start:
            self.assertEqual(api.dispatch(self.event('/research/studies',method='POST',body=body),self.actor),{'id':body['request_id']});start.assert_called_once_with(self.actor,body)
        with patch.object(store,'rows',return_value=[{'id':self.study,'study_kind':'paper'}]),patch.object(api.preparation,'status',return_value={'retained':True}) as status:
            self.assertEqual(self.call('prepare',method='GET'),{'retained':True});status.assert_called_once_with({'id':self.study,'study_kind':'paper'})
        for kind in ['idea','claim-design']:
            module=api.ideas if kind=='idea' else api.claim_design
            with patch.object(store,'rows',return_value=[{'id':self.study,'study_kind':'idea' if kind=='idea' else 'paper'}]),patch.object(api.claim_design,'context',return_value={}),patch.object(module,'status',return_value={'retained':kind}) as status:
                self.assertEqual(self.call(kind,method='GET'),{'retained':kind});status.assert_called_once_with(self.study)
        with patch.object(api.scheduler,'enqueue',return_value={'job':'original'}) as enqueue:
            self.assertEqual(self.call('queue',body={'request_id':'original'}),{'job':'original'});enqueue.assert_called_once_with(self.study,self.actor,{'request_id':'original'})

    def test_compiling_an_existing_protocol_without_new_original_configuration_never_creates_a_private_artifact(self):
        document={'original':'retained protocol'}
        with patch.object(api.network_spec,'bind_inputs',return_value=document) as bind,patch.object(store,'artifact') as upload:
            self.assertEqual(self.call('network',body={'action':'compile','document':document})['document'],document)
        bind.assert_called_once_with(document);upload.assert_not_called()

    def test_authentication_requires_google_service_verified_identity_and_preserves_header_secret(self):
        event = self.event(); event['headers'] = {'aUtHoRiZaTiOn': 'Bearer synthetic-access-token'}
        for status, expected in [(401, 401), (403, 401), (429, 503), (503, 503)]:
            with patch.object(api.urllib.request, 'urlopen', side_effect=urllib.error.HTTPError('https://private.example.test', status, 'Private identity', {}, None)):
                self.assert_http(expected, lambda: api.authenticate(event))
        response = io.BytesIO(canonical(dict(id=self.actor, identities=[{'provider': 'google'}])))
        with patch.object(api.urllib.request, 'urlopen', return_value=response) as open: self.assertEqual(api.authenticate(event), self.actor)
        request = open.call_args.args[0]; self.assertEqual(request.full_url, 'https://' + store.PROJECT + '.supabase.co/auth/v1/user'); self.assertEqual(request.get_header('Authorization'), 'Bearer synthetic-access-token'); self.assertEqual(open.call_args.kwargs, {'timeout': 8})
        for user in [dict(id=self.actor, is_anonymous=True, identities=[{'provider': 'google'}]), dict(identities=[{'provider': 'google'}]), dict(id=self.actor, identities=[{'provider': 'email'}])]:
            with patch.object(api.urllib.request, 'urlopen', return_value=io.BytesIO(canonical(user))): self.assert_http(403, lambda: api.authenticate(event))
        with patch.dict(os.environ, {'SUPABASE_URL': 'https://other.supabase.co'}): self.assert_http(503, lambda: api.authenticate(event))
        for token in ['', 'Basic synthetic', 'Bearer ' + 'x' * 8192]:
            event['headers']['aUtHoRiZaTiOn'] = token; self.assert_http(401, lambda: api.authenticate(event))

    def test_json_body_bounds_reject_nonfinite_nonobject_and_nonutf8_transport(self):
        self.assertEqual(api.parse_body(self.event()), {})
        for event, status in [(dict(body='{}', isBase64Encoded=True), 400), (dict(body={'unexpected': True}), 413), (dict(body='α' * (api.MAX_BODY // 2 + 1)), 413)]: self.assert_http(status, lambda event=event: api.parse_body(event))
        for raw in ['[]', 'null', '{"value":NaN}', '{"value":Infinity}', 'invalid JSON']:
            with self.subTest(raw=raw), self.assertRaises(ValueError): api.parse_body({'body': raw})

    def test_campaign_replay_requires_both_original_input_and_campaign_and_never_reexecutes(self):
        request_id, campaign_id = uid(), uid(); body = dict(request_id=request_id, campaign_id=campaign_id, raw_input='{ "retained": 0 }\n'); prior = dict(input_sha256=digest(body['raw_input'].encode()), campaign_id=campaign_id)
        with patch.object(store, 'rows', return_value=[prior]), patch.object(api.runner, 'execute') as execute:
            self.assertEqual(api.campaign_run(self.study, self.actor, body), dict(run=prior, replayed=True))
            self.assert_http(409, lambda: api.campaign_run(self.study, self.actor, dict(body, raw_input='changed'))); self.assert_http(409, lambda: api.campaign_run(self.study, self.actor, dict(body, campaign_id=uid()))); execute.assert_not_called()
        with patch.object(store, 'rows', side_effect=[[], []]): self.assert_http(404, lambda: api.campaign_run(self.study, self.actor, body))
        with patch.object(store, 'rows', side_effect=[[], [{'protocol_id': uid()}], []]): self.assert_http(409, lambda: api.campaign_run(self.study, self.actor, body))
        for limits in [dict(max_observations=1001, wall_seconds=10), dict(max_observations=1000, wall_seconds=11)]:
            with patch.object(store, 'rows', side_effect=[[], [{'protocol_id': uid()}], [{'document': {'resource_limits': limits}}]]), patch.object(api.runner, 'execute') as execute: self.assert_http(400, lambda: api.campaign_run(self.study, self.actor, body)); execute.assert_not_called()

    def test_owner_autonomy_routes_use_exact_task_call_and_server_owned_cancellation(self):
        task, call = uid(), uid()
        with patch.object(api.autonomy,'overview',return_value={'retained':True}) as overview:self.assertEqual(self.call('autonomy','GET'),{'retained':True});overview.assert_called_once_with([{'id':self.study}])
        with patch.object(api.autonomy, 'builder_evidence', return_value={'original': True}) as evidence:
            self.assertEqual(self.call('autonomy', 'GET', query={'task': task, 'builder_call': call, 'kind': 'response', 'cursor': '2'}), {'original': True}); evidence.assert_called_once_with(self.study, task, call, 'response', '2')
        with patch.object(api.autonomy, 'history', return_value={'history': True}) as history: self.assertEqual(self.call('autonomy', 'GET', query={'task': task}), {'history': True}); history.assert_called_once_with(self.study, task, None)
        with patch.object(store, 'rpc', return_value={'cancel_requested': True}) as rpc:
            self.assertEqual(self.call('autonomy', body={'action': 'cancel'}), {'cancel_requested': True}); rpc.assert_called_once_with('research_autonomy_control', dict(p_operation='cancel', p_actor=self.actor, p_study=self.study, p_payload={}))
            self.assert_http(400, lambda: self.call('autonomy', body={'action': 'cancel', 'deadline': 'changed'})); self.assertEqual(rpc.call_count, 1)
        self.owns.return_value = False; self.assert_http(404, lambda: self.call('autonomy', 'GET'))

    def test_public_study_pagination_and_missing_shared_task_history_preserve_explicit_coverage(self):
        with patch.object(store,'rows',return_value=[{'id':uid()} for _ in range(101)]) as read:
            result=api.dispatch(dict(rawPath='/research/studies',requestContext={'http':{'method':'GET'}}),None);self.assertEqual(len(result['studies']),100);self.assertTrue(result['has_more']);self.assertEqual(result['access'],'published');self.assertTrue(read.call_args.kwargs['public'])
        with patch.object(api.scheduler,'public_history',return_value=None):self.assert_http(404,lambda:self.call('autonomy','GET',query={'public':'1','task':uid()}))

    def test_gap_routes_keep_design_and_execution_adapters_distinct_and_reject_unsupported_methods(self):
        claim,gap=uid(),uid()
        with patch.object(api.claim_design,'selection',return_value=({'selected':True},{'private':True})) as select:self.assertEqual(self.call('gap-experiments','GET',query={'claim_id':claim}),{'selected':True});select.assert_called_once_with(self.study,self.actor,claim)
        with patch.object(api.gap_queue,'preview',return_value={'preview':True}) as preview:self.assertEqual(self.call('gap-experiments','GET',query={'gap_id':gap}),{'preview':True});preview.assert_called_once_with(self.study,self.actor,gap)
        for action,adapter,name in [('design',api.claim_design,'start'),('enqueue',api.gap_queue,'enqueue')]:
            with patch.object(adapter,name,return_value={'original':action}) as operation:self.assertEqual(self.call('gap-experiments',body={'action':action}),{'original':action});operation.assert_called_once_with(self.study,self.actor,{'action':action})
        self.assert_http(405,lambda:self.call('gap-experiments','DELETE'))

    def test_network_actions_require_owner_and_compile_preserves_original_decimal_configuration(self):
        for action, method in [('upload-package', 'upload'), ('upload-input', 'upload_input'), ('cancel', 'cancel')]:
            body = {'action': action, 'request_id': uid()}
            with patch.object(api.network_execution, method, return_value={'recorded': True}) as operation: self.assertEqual(self.call('network', body=body), {'recorded': True}); operation.assert_called_once_with(self.study, self.actor, {'request_id': body['request_id']})
        self.assert_http(405, lambda: self.call('network', 'GET'))
        for body in [{'action': 'unknown'}, {'action': 'compile', 'document': {}, 'extra': True}, {'action': 'compile'}]:
            with self.subTest(body=body), self.assertRaises(ValueError): self.call('network', body=body)
        document = networking.document(); original = copy.deepcopy(document); configuration = document['configurations'][0]; raw = json.dumps(configuration['details']['test_config'], indent=2)
        artifact = dict(id=uid(), sha256=digest(raw.encode()))
        with patch.object(store, 'artifact', return_value=artifact) as retain, patch.object(store, 'append') as append:
            result = self.call('network', body=dict(action='compile', document=document, original_configuration=raw, configuration_identity=configuration['identity'])); retain.assert_called_once(); self.assertEqual(retain.call_args.args[2], raw.encode()); append.assert_called_once_with(self.study, self.actor, 'artifacts', artifact)
        bound = result['document']; self.assertEqual(bound['hypotheses'], original['hypotheses']); self.assertEqual(bound['statistics'], original['statistics']); self.assertIn(dict(identity='original-network-config:' + configuration['identity'], sha256=artifact['sha256'], artifact_id=artifact['id']), bound['input_versions'])
        for raw_value in [123, 'x' * 24001, '{"num_clients":NaN}', '{"num_clients":2}', '{"num_clients":1.234567890123456789}']:
            with patch.object(store, 'artifact') as retain, self.subTest(raw=raw_value if not isinstance(raw_value, str) or len(raw_value) < 100 else 'oversized'), self.assertRaises(ValueError): self.call('network', body=dict(action='compile', document=original, original_configuration=raw_value, configuration_identity=configuration['identity']))
            retain.assert_not_called()
        self.owns.return_value = False; self.assert_http(404, lambda: self.call('network', body={'action': 'cancel'}))

    def test_planning_routes_preserve_paper_idea_origin_and_exact_owner_scoped_inputs(self):
        for origin, route, adapter in [('paper', 'claim-design', api.claim_design), ('idea', 'idea', api.ideas)]:
            study = dict(id=self.study, study_kind=origin)
            with patch.object(store, 'rows', return_value=[study]), patch.object(api.claim_design, 'context', return_value={'retained': True}) as retained:
                for action, method in [('save', 'save'), ('accept', 'accept'), *([('adopt', 'adopt')] if origin == 'paper' else [])]:
                    with patch.object(adapter, method, return_value={'action': action}) as operation: self.assertEqual(self.call(route, body={'action': action}), {'action': action}); operation.assert_called_once_with(self.study, self.actor, {'action': action})
                for action, method in [('prepare-campaign', 'prepare_campaign'), ('resume-campaign', 'resume_campaign')]:
                    with patch.object(api.ideas, method, return_value={'prepared': True}) as operation: self.assertEqual(self.call(route, body={'action': action}), {'prepared': True}); operation.assert_called_once_with(study, self.actor, {'action': action}, retained={'retained': True} if origin == 'paper' else None)
                with self.assertRaisesRegex(ValueError, 'Choose save'): self.call(route, body={'action': 'unknown'})
                self.assert_http(404, lambda: self.call(route, 'DELETE'))
            with patch.object(store, 'rows', return_value=[dict(study, study_kind='unrelated')]), patch.object(api.claim_design, 'context', return_value={}): self.assert_http(409, lambda: self.call(route, 'GET'))

    def test_preparation_queue_and_record_routes_keep_execution_records_under_the_verified_runner(self):
        study = dict(id=self.study, study_kind='paper')
        with patch.object(store, 'rows', return_value=[study]):
            for action, method in [('prepare', 'prepare'), ('enqueue', 'enqueue')]:
                with patch.object(api.preparation, method, return_value={'prepared': True}) as operation: self.assertEqual(self.call('prepare', body={'action': action}), {'prepared': True}); operation.assert_called_once_with(study, self.actor, {'action': action})
            with self.assertRaisesRegex(ValueError, 'Choose prepare or enqueue'): self.call('prepare', body={'action': 'unknown'})
            self.assert_http(404, lambda: self.call('prepare', 'DELETE'))
        with patch.object(store, 'rows', return_value=[]): self.assert_http(404, lambda: self.call('prepare', 'GET'))
        with patch.object(store, 'rows', return_value=[dict(study, study_kind='idea')]): self.assert_http(409, lambda: self.call('prepare', 'GET'))
        for kind in ['protocols', 'runs', 'measurements', 'summaries', 'published_values', 'artifacts', 'publications']: self.assert_http(400, lambda kind=kind: self.call('records', body={'kind': kind, 'record': {}}))
        with patch.object(store, 'append', return_value={'id': 'validated-record'}) as append: self.assertEqual(self.call('records', body={'kind': 'claims', 'record': {'prospective': True}}), {'id': 'validated-record'}); append.assert_called_once_with(self.study, self.actor, 'claims', {'prospective': True})
        with patch.object(api.scheduler, 'action', return_value={'cancelled': True}) as action: self.assertEqual(self.call('queue-actions', body={'action': 'cancel'}), {'cancelled': True}); action.assert_called_once_with(self.study, self.actor, {'action': 'cancel'})

    def test_publication_reasons_and_frozen_protocol_creation_keep_original_criteria_and_request_ids(self):
        raw, protocol, campaign = numerical.fixture(); body = dict(request_id=protocol['id'], document=protocol['document'])
        with patch.object(store, 'rows', return_value=[]), patch.object(store, 'append', return_value=protocol) as append: self.assertEqual(self.call('protocols', body=body), protocol); self.assertEqual(append.call_args.args[:3], (self.study, self.actor, 'protocols')); self.assertEqual(append.call_args.args[3]['document'], protocol['document']); self.assertEqual(append.call_args.args[3]['sha256'], digest(canonical(protocol['document'])))
        request = dict(request_id=uid(), scientific_review_id=uid(), software_review_id=uid(), reason='Retained reviewed evidence scope', withdraw=True)
        for defect in [dict(withdraw='true'), dict(reason=''), dict(reason='x' * 5001)]:
            with self.subTest(defect=defect), self.assertRaises(ValueError): self.call('publish', body=dict(request, **defect))
        with patch.object(store, 'rpc', return_value={'published': True}) as rpc: self.assertEqual(self.call('publish', body=request), {'published': True}); payload = rpc.call_args.args[1]; self.assertTrue(payload['p_withdraw']); self.assertEqual(payload['p_actor'], self.actor); self.assertEqual(payload['p_reason'], request['reason']); self.assertEqual(payload['p_id'], request['request_id'])

    def test_handler_authenticates_writes_and_never_leaks_storage_or_unexpected_exception_details(self):
        event = self.event(method='POST', body={})
        with patch.object(api, 'authenticate', return_value=self.actor) as authenticate, patch.object(api, 'dispatch', return_value={'recorded': True}) as dispatch:
            response = api.handler(event); self.assertEqual(response['statusCode'], 200); authenticate.assert_called_once_with(event); dispatch.assert_called_once_with(event, self.actor); self.assertEqual(response['headers']['Cache-Control'], 'no-store')
        for failure, status, public_text in [(ValueError('Invalid original request'), 400, 'Invalid original request'), (store.StoreError('Private SQL or artifact path'), 503, 'Research storage is unavailable'), (urllib.error.URLError('Private URL'), 503, 'Research storage is unavailable'), (RuntimeError('Private owner or credential'), 503, 'Research request failed')]:
            with patch.object(api, 'dispatch', side_effect=failure), contextlib.redirect_stdout(io.StringIO()) as log:
                response = api.handler(self.event()); self.assertEqual(response['statusCode'], status); self.assertIn(public_text, json.loads(response['body'])['error']); self.assertNotIn('Private', response['body']); self.assertNotIn('Private', log.getvalue())

    def test_notification_projection_rejects_bad_workflow_coverage_and_bounded_pagination_failures(self):
        workflow = dict(study_id=self.study, status='running', updated_at='2026-11-01T00:00:00Z')
        for workflows in [[workflow] * 101, [workflow, workflow], [dict(workflow, study_id=uid())], [dict(workflow, status='unknown')]]:
            with patch.object(store, 'rows', return_value=workflows), self.subTest(workflows=len(workflows)), self.assertRaisesRegex(store.StoreError, 'notification'): api.assessment_statuses([{'id': self.study}])
        task = dict(id=uid(), study_id=self.study, status='queued', label=None, claim_id=None)
        with patch.object(store, 'rows', side_effect=[[workflow], [task] * 501]), self.assertRaisesRegex(store.StoreError, 'pagination is invalid'): api.assessment_statuses([{'id': self.study}])
        for per_page in [1, 500]:
            counter = 0
            def rows(kind, params=None, public=False):
                nonlocal counter
                if kind == 'autonomy_workflows': return [workflow]
                result = []
                for _ in range(min(per_page, params['limit'])):
                    counter += 1; result.append(dict(task, id=str(uuid.UUID(int=counter))))
                return result
            with patch.object(store, 'rows', side_effect=rows), self.subTest(per_page=per_page), self.assertRaisesRegex(store.StoreError, 'interactive bound'): api.assessment_statuses([{'id': self.study}])

    def test_private_upload_and_study_creation_do_not_accept_unavailable_autonomy_or_client_policy_overrides(self):
        upload = self.event('/research/paper-uploads', 'POST', {'action': 'upload'})
        with patch.object(api.paper_uploads, 'submit') as submit: self.assert_http(503, lambda: api.dispatch(upload, self.actor)); submit.assert_not_called()
        with patch.object(api.paper_uploads, 'submit', return_value={'review': True}) as submit:
            review = self.event('/research/paper-uploads', 'POST', {'action': 'review'}); self.assertEqual(api.dispatch(review, self.actor), {'review': True}); submit.assert_called_once_with(self.actor, {'action': 'review'})
        paper = dict(title='Synthetic prospective paper', domain='Networking', scope='Synthetic finite software fixture', paper_url='https://example.test/paper'); body = dict(request_id=uid(), paper=paper)
        with patch.object(store, 'rpc', return_value={'id': body['request_id']}) as rpc:
            self.assertEqual(api.dispatch(self.event('/research/studies', 'POST', body), self.actor), {'id': body['request_id']}); self.assertEqual(rpc.call_args.args[0], 'research_create_study'); self.assertEqual(rpc.call_args.args[1], dict(p_actor=self.actor, p_id=body['request_id'], p_document=dict(paper, origin_module=None)))
            with self.assertRaisesRegex(ValueError, 'application defaults'): api.dispatch(self.event('/research/studies', 'POST', dict(body, autonomous=False)), self.actor)
            self.assertEqual(rpc.call_count, 1)

    def test_public_builder_evidence_is_redacted_while_private_queue_and_snapshots_keep_owner_checks(self):
        task, call = uid(), uid(); query = dict(public='1', task=task, builder_call=call); event = self.event(self.path + '/autonomy', query=query)
        with patch.object(api.scheduler, 'shared_paper', return_value=None), patch.object(api.autonomy, 'builder_evidence') as evidence: self.assert_http(404, lambda: api.dispatch(event)); evidence.assert_not_called()
        original = {'zero': 0, 'missing': None, 'actor_id': 'private-owner', 'request': {'original_sha256': 'a' * 64}}
        with patch.object(api.scheduler, 'shared_paper', return_value={'id': self.study}), patch.object(api.autonomy, 'builder_evidence', return_value=original) as evidence:
            result = api.dispatch(event); self.assertEqual(result['access'], 'public-read-only'); self.assertEqual(result['zero'], 0); self.assertIsNone(result['missing']); self.assertEqual(result['actor_id'], '[redacted]'); self.assertEqual(original['actor_id'], 'private-owner'); self.assertEqual(result['projection']['redacted'], 1); evidence.assert_called_once_with(self.study, task, call, 'request', '0')
        for query in [{}, {'title_study': self.study}]:
            event = self.event('/research/queue', query=query); self.assert_http(401, lambda: api.dispatch(event))
            with patch.object(api.scheduler, 'overview', return_value={'queue': True}) as overview: self.assertEqual(api.dispatch(event, self.actor), {'queue': True}); overview.assert_called_once_with(*((self.actor, self.study) if query else (self.actor,)))
        for actor, owner, status in [(None, False, 401), (self.actor, False, 404)]:
            self.owns.return_value = owner; self.assert_http(status, lambda actor=actor: api.dispatch(self.event(self.path + '/queue'), actor))
        self.owns.return_value = True
        with patch.object(api.scheduler, 'snapshot', return_value={'queue': True}) as snapshot: self.assertEqual(self.call('queue', 'GET'), {'queue': True}); snapshot.assert_called_once_with(self.study, self.actor)
        with patch.object(store, 'snapshot', return_value={'study': {'id': self.study}}), patch.object(store, 'rows', return_value=[{'study_id': self.study}]): self.assertTrue(api.dispatch(self.event(), self.actor)['study']['claim_design'])
        with patch.object(store, 'snapshot', return_value=None): self.assert_http(404, lambda: api.dispatch(self.event(), self.actor))
        with patch.object(api, 'campaign_run', return_value={'run': True}) as run: self.assertEqual(self.call('runs', body={'original': True}), {'run': True}); run.assert_called_once_with(self.study, self.actor, {'original': True})
        self.assert_http(404, lambda: self.call('not-a-route', body={}))


if __name__ == '__main__': unittest.main()
