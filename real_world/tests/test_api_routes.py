"""Route/auth/idempotency regressions using synthetic boundary adapters."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import unittest
import urllib.error
import uuid
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import api
import config
import research_execution
from test_real_world import AwsError, job, settings
from test_research_execution import event as saved_event, saved

IDENTITY = '3705a384-081d-4d8c-b75d-7e576c149151'


def event(path='/real-world/regions', method='GET', **extra):
    return dict(rawPath=path, requestContext={'http': {'method': method}}, **extra)


class Authentication(unittest.TestCase):
    def test_missing_malformed_and_oversized_bearers_never_reach_the_auth_provider(self):
        for headers in [{}, {'Authorization': 'Basic private'}, {'Authorization': 'Bearer ' + 'x' * 8193}]:
            with self.subTest(headers=list(headers)), patch.object(api.urllib.request, 'urlopen') as open_request, self.assertRaises(api.HttpError) as caught: api.authenticate({'headers': headers})
            self.assertEqual(caught.exception.status, 401); open_request.assert_not_called()

    def test_verified_google_identity_comes_from_supabase_and_other_sessions_are_rejected(self):
        with patch.dict(os.environ, SUPABASE_URL='https://auth.example.test', SUPABASE_ANON_KEY='synthetic-public-key'):
            for user, status in [({'id': 'verified-owner', 'identities': [{'provider': 'google'}]}, None), ({'id': 'anonymous', 'is_anonymous': True, 'identities': [{'provider': 'google'}]}, 403), ({'identities': [{'provider': 'google'}]}, 403), ({'id': 'other-provider', 'identities': [{'provider': 'email'}]}, 403)]:
                with self.subTest(user=user), patch.object(api.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps(user).encode())) as open_request:
                    if status:
                        with self.assertRaises(api.HttpError) as caught: api.authenticate({'headers': {'AUTHORIZATION': 'Bearer synthetic-session'}})
                        self.assertEqual(caught.exception.status, status)
                    else: self.assertEqual(api.authenticate({'headers': {'AUTHORIZATION': 'Bearer synthetic-session'}}), 'verified-owner')
                    request = open_request.call_args.args[0]
                    self.assertEqual(request.full_url, 'https://auth.example.test/auth/v1/user'); self.assertEqual(request.get_header('Authorization'), 'Bearer synthetic-session')
                    self.assertEqual(request.get_header('Apikey'), 'synthetic-public-key'); self.assertEqual(open_request.call_args.kwargs, {'timeout': 8})
            for code in [401, 403, 503]:
                error = urllib.error.HTTPError('https://auth.example.test', code, 'Synthetic response', {}, None)
                with self.subTest(code=code), patch.object(api.urllib.request, 'urlopen', side_effect=error):
                    with self.assertRaises(api.HttpError if code != 503 else urllib.error.HTTPError) as caught: api.authenticate({'headers': {'authorization': 'Bearer synthetic-session'}})
                    if code != 503: self.assertEqual(caught.exception.status, 401)


class RouteContracts(unittest.TestCase):
    def setUp(self):
        self.value = dict(job(), job_id=IDENTITY, schema_version=config.SCHEMA_VERSION)
        self.load = self.enterContext(patch.object(api.store, 'load', return_value=self.value))
        self.detail = self.enterContext(patch.object(api.store, 'detail', return_value={'job_id': IDENTITY, 'status_history': []}))

    def test_catalog_and_list_routes_preserve_public_and_owner_scopes(self):
        with patch.object(api.cloud, 'regions', return_value=['synthetic-region']) as regions, patch.object(api.cloud, 'locations', return_value=['synthetic-zone']) as locations, patch.object(api.store, 'page', return_value={'tests': []}) as page:
            self.assertEqual(api.dispatch(event('/real-world/regions/'), None), {'regions': ['synthetic-region']})
            self.assertEqual(api.dispatch(event('/real-world/locations', queryStringParameters={'region': 'us-east-1'}), None), {'zones': ['synthetic-zone']})
            locations.assert_called_once_with('us-east-1'); regions.assert_called_once()
            api.dispatch(event('/real-world/locations'), None); locations.assert_called_with('')
            api.dispatch(event('/real-world/tests', queryStringParameters={'cursor': 'owner-cursor'}), 'verified-owner'); page.assert_called_with('owner-cursor', 'verified-owner')
            api.dispatch(event('/real-world/reports', queryStringParameters={'cursor': 'public-cursor'}), None); page.assert_called_with('public-cursor')
        self.load.assert_not_called()

    def test_untrusted_json_and_unknown_routes_fail_before_any_launch(self):
        bad_events = [event('/real-world/tests', 'POST', isBase64Encoded=True, body='e30='), event('/real-world/tests', 'POST', body='x' * 20001), event('/real-world/tests', 'POST', body='[]'), event('/real-world/tests', 'POST', body='{')]
        with patch.object(api, 'launch') as launch:
            for request in bad_events:
                with self.subTest(request=request.get('isBase64Encoded')), self.assertRaises(ValueError): api.dispatch(request, 'verified-owner')
            launch.assert_not_called()
            launch.return_value = {'retained': True}
            self.assertEqual(api.dispatch(event('/real-world/tests', 'POST'), 'verified-owner'), {'retained': True}); launch.assert_called_with({}, 'verified-owner')
            self.assertEqual(api.dispatch(event('/real-world/tests', 'POST', body='{"request_id":"original"}'), 'verified-owner'), {'retained': True}); launch.assert_called_with({'request_id': 'original'}, 'verified-owner')
        for path in ['/unknown', '/real-world/tests/' + IDENTITY + '/unknown', '/real-world/reports/' + IDENTITY + '/unknown']:
            with self.subTest(path=path), self.assertRaises(api.HttpError) as caught: api.dispatch(event(path), 'verified-owner')
            self.assertEqual(caught.exception.status, 404)
        with self.assertRaises(api.HttpError) as caught: api.dispatch(event('/real-world/tests'), None)
        self.assertEqual(caught.exception.status, 401)

    def test_missing_and_old_schema_jobs_have_no_public_detail_report_or_artifact_capabilities(self):
        with patch.object(api, 'measurement_artifacts') as downloads:
            for value in [None, dict(self.value, schema_version=0)]:
                self.load.return_value = value
                for kind in ['tests', 'reports']:
                    for suffix in ['', '/artifacts']:
                        with self.subTest(kind=kind, suffix=suffix, value=value), self.assertRaises(api.HttpError) as caught: api.dispatch(event('/real-world/' + kind + '/' + IDENTITY + suffix), None)
                        self.assertEqual(caught.exception.status, 404)
            self.detail.assert_not_called(); downloads.assert_not_called()
        for value in [None, dict(self.value, owner='another-owner')]:
            self.load.return_value = value
            with self.assertRaises(api.HttpError): api.owned(IDENTITY, 'verified-owner')
        self.load.return_value = dict(self.value, owner='verified-owner'); self.assertEqual(api.owned(IDENTITY, 'verified-owner')['job_id'], IDENTITY)

    def test_detail_and_artifact_reads_are_public_while_terminal_cancellation_is_idempotent(self):
        with patch.object(api, 'measurement_artifacts', return_value={'artifacts': []}) as downloads:
            for kind in ['tests', 'reports']:
                self.assertEqual(api.dispatch(event('/real-world/' + kind + '/' + IDENTITY + '/artifacts'), None), {'artifacts': []})
            self.assertEqual(downloads.call_count, 2)
        with patch('reports.load_report', return_value={'summary': {'missing': None, 'zero': 0}}) as report:
            self.assertEqual(api.dispatch(event('/real-world/reports/' + IDENTITY, queryStringParameters={'summary': '1'}), None)['summary'], {'missing': None, 'zero': 0})
            report.assert_called_once_with(self.value, summary_only=True)
        for status in config.TERMINAL:
            self.value['status'] = status
            with self.subTest(status=status), patch.object(api.store, 'cancel') as cancel:
                result = api.dispatch(event('/real-world/tests/' + IDENTITY + '/cancel', 'POST'), self.value['owner'])
            self.assertTrue(result['can_manage']); cancel.assert_not_called()

    def test_handler_authenticates_optional_bearers_and_returns_stable_errors_without_private_payloads(self):
        with patch.object(api, 'authenticate', return_value='verified-owner') as authenticate, patch.object(api, 'dispatch', return_value={'zero': 0}) as dispatch:
            result = api.handler(event(headers={'AUTHORIZATION': 'Bearer synthetic-session'}), None)
            self.assertEqual(result['statusCode'], 200); self.assertEqual(json.loads(result['body']), {'zero': 0}); dispatch.assert_called_with(event(headers={'AUTHORIZATION': 'Bearer synthetic-session'}), 'verified-owner')
            authenticate.assert_called_once()
        for error, status in [(api.HttpError(404, 'Test not found.'), 404), (ValueError('Invalid fixture input'), 400), (KeyError('missing'), 400), (TypeError('invalid'), 400), (RuntimeError('synthetic internal exception'), 503)]:
            with self.subTest(error=error), patch.object(api, 'dispatch', side_effect=error), contextlib.redirect_stdout(io.StringIO()): response = api.handler(event(), None)
            self.assertEqual(response['statusCode'], status); self.assertEqual(response['headers']['Cache-Control'], 'no-store')
            if status == 503: self.assertNotIn('synthetic internal exception', response['body'])
        with patch.object(research_execution, 'envelope', return_value=True), patch.object(research_execution, 'handle', return_value={'status': 'launch-unconfirmed'}) as handle, patch.object(api, 'authenticate') as authenticate:
            response = api.handler(saved_event(), None)
        self.assertEqual(response['statusCode'], 200); handle.assert_called_once_with(saved_event(), api.launch); authenticate.assert_not_called()


class LaunchReconciliation(unittest.TestCase):
    def setUp(self):
        self.body = dict(config=settings(), request_id=IDENTITY); self.owner = 'verified-owner'
        self.identity = str(uuid.uuid5(uuid.NAMESPACE_URL, self.owner + ':' + IDENTITY))
        self.value = dict(job(), owner=self.owner, job_id=self.identity, schema_version=1)
        self.client = MagicMock()
        self.enterContext(patch.object(api.cloud, 'client', return_value=self.client))
        self.enterContext(patch.dict(os.environ, STATE_MACHINE_ARN='synthetic-state-machine', RUNTIME_REVISION='frozen-synthetic-runtime'))
        self.enterContext(patch.object(api.time, 'time', return_value=123))
        self.catalog = self.enterContext(patch.object(api.cloud, 'locations', return_value=[dict(zone_id='use1-az1', available=True, instance_types=['t3.medium'])]))

    def test_invalid_request_ids_and_configuration_shapes_never_start_execution(self):
        with patch.object(api.store, 'load') as load:
            for request in ['', None, {}, 'not-a-uuid']:
                with self.subTest(request=request), self.assertRaisesRegex(ValueError, 'valid request ID'): api.launch(dict(self.body, request_id=request), self.owner)
            for value in [None, {}, dict(settings(), extra=True), dict(settings(), server=None), dict(settings(), server=dict(settings()['server'], region=None))]:
                with self.subTest(value=value), self.assertRaises(ValueError): config.validate_config(value)
            load.assert_not_called(); self.client.start_execution.assert_not_called()

    def test_lost_create_acknowledgement_reuses_the_original_job_and_detects_conflicting_race(self):
        with patch.object(api.store, 'load', side_effect=[None, self.value]), patch.object(api.store, 'create', return_value=False) as create:
            result = api.launch(self.body, self.owner)
        self.assertEqual(result['job_id'], self.identity)
        proposed = create.call_args.args[0]; self.assertEqual(proposed['deadline'], 2823); self.assertEqual(proposed['runtime_revision'], 'frozen-synthetic-runtime')
        self.client.start_execution.assert_called_once_with(stateMachineArn='synthetic-state-machine', name=self.identity, input=json.dumps({'job_id': self.identity}, sort_keys=True, separators=(',', ':')))
        conflicting = copy.deepcopy(self.value); conflicting['config']['rate_mbit'] += 1
        self.client.reset_mock()
        with patch.object(api.store, 'load', side_effect=[None, conflicting]), patch.object(api.store, 'create', return_value=False), self.assertRaises(api.HttpError) as caught: api.launch(self.body, self.owner)
        self.assertEqual(caught.exception.status, 409); self.client.start_execution.assert_not_called()

    def test_duplicate_execution_acknowledgement_is_reconciled_but_other_cloud_errors_propagate(self):
        with patch.object(api.store, 'load', return_value=self.value):
            self.client.start_execution.side_effect = AwsError('ExecutionAlreadyExists'); self.assertEqual(api.launch(self.body, self.owner)['job_id'], self.identity)
            self.client.start_execution.side_effect = AwsError('AccessDeniedException')
            with self.assertRaises(AwsError): api.launch(self.body, self.owner)
        self.catalog.assert_not_called()
        self.client.reset_mock()
        for status in config.TERMINAL:
            with self.subTest(status=status), patch.object(api.store, 'load', return_value=dict(self.value, status=status)):
                self.assertEqual(api.launch(self.body, self.owner)['status'], status)
        self.client.start_execution.assert_not_called()

    def test_saved_deadline_and_cancel_requests_cancel_the_original_job_without_a_new_launch(self):
        value = dict(self.value, created_at=1, updated_at=2, status='running')
        for action, now in [('cancel', 0), ('status', 5_000_000_000)]:
            launch = Mock()
            with self.subTest(action=action), patch.object(research_execution.database, 'base_url', return_value='https://regphejnlvfpyokpniny.supabase.co'), patch.object(research_execution.database, 'rpc', return_value=saved()), patch.object(research_execution.store, 'load', return_value=value), patch.object(research_execution.store, 'cancel') as cancel, patch.object(research_execution.time, 'time', return_value=now):
                result = research_execution.handle(saved_event(action), launch)
            cancel.assert_called_once_with(result['external_job_id']); launch.assert_not_called()
        with patch.object(api.artifacts, 'downloads', return_value={'artifacts': []}) as downloads:
            self.assertEqual(api.measurement_artifacts(value), {'artifacts': []})
        downloads.assert_called_once_with(value)
        launch = Mock()
        with patch.object(research_execution.database, 'base_url', return_value='https://regphejnlvfpyokpniny.supabase.co'), patch.object(research_execution.database, 'rpc', return_value=None), self.assertRaisesRegex(ValueError, 'Saved networking invocation required'):
            research_execution.handle(saved_event(), launch)
        launch.assert_not_called()


if __name__ == '__main__': unittest.main()
