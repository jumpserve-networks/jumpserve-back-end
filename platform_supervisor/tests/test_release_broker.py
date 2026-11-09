"""Exact workflow identities and credential-separated release proof downloads."""
import copy
import io
from pathlib import Path
import sys
import unittest
import urllib.error
from unittest.mock import MagicMock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from platform_supervisor import releases
from platform_supervisor.common import Hold, POLICY, canonical

BACK = 'jumpserve-networks/jumpserve-back-end'
FRONT = 'jumpserve-networks/jumpserve-front-end'
INFRA = POLICY['release_repository']


def archive(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as result:
        for name, raw in files.items(): result.writestr(name, raw)
    return buffer.getvalue()


class ReleaseBroker(unittest.TestCase):
    def setUp(self):
        self.github = MagicMock(); self.github.token = 'synthetic-header-only-token'; self.broker = releases.Releases(self.github)
        self.operation = dict(id='synthetic-operation', request=dict(component='frontend', repository=FRONT, revision='a' * 40, workflow_revision='b' * 40))
        self.run = dict(id=23, display_title='Platform release synthetic-operation', head_sha='b' * 40, status='completed', conclusion='success')
        self.runs = [self.run]; self.artifacts = [dict(name='platform-release-synthetic-operation', expired=False, size_in_bytes=500, id=42)]
        self.github.get.side_effect = self.get; self.github.main.return_value = 'b' * 40
        self.proof = dict(passed=True, operation_id='synthetic-operation', **{name: self.operation['request'][name] for name in ['component', 'repository', 'revision']})
        self.github.opener.open.side_effect = lambda request, **options: io.BytesIO(archive({'production-verification.json': canonical(self.proof)}))
    def get(self, repository, path):
        if '/runs?' in path: return {'workflow_runs': copy.deepcopy(self.runs)}
        if '/artifacts?' in path: return {'artifacts': copy.deepcopy(self.artifacts)}
        raise AssertionError(path)

    def test_find_requires_one_run_at_the_exact_executor_revision_and_never_treats_truncated_history_as_absent(self):
        self.assertEqual(self.broker.find(self.operation)['id'], 23)
        self.runs = []; self.assertIsNone(self.broker.find(self.operation))
        self.runs = [self.run, self.run]
        with self.assertRaisesRegex(Hold, 'Multiple release runs'): self.broker.find(self.operation)
        self.runs = [dict(self.run, head_sha='c' * 40)]
        with self.assertRaisesRegex(Hold, 'executor revision changed'): self.broker.find(self.operation)
        unrelated = [dict(self.run, id=index, display_title='Unrelated original workflow') for index in range(100)]
        self.github.get.side_effect = [{'workflow_runs': unrelated}, {'workflow_runs': [self.run]}]
        self.assertEqual(self.broker.find(self.operation)['id'], 23)
        self.github.get.side_effect = lambda *args: {'workflow_runs': unrelated}
        with self.assertRaisesRegex(Hold, 'history.*limit') as caught: self.broker.find(self.operation)
        self.assertEqual(caught.exception.status, 'reconciling')
        with self.assertRaisesRegex(Hold, 'history.*limit'): self.broker.dispatch(self.operation)
        self.github.post.assert_not_called()

    def test_release_plan_keeps_specialized_runtime_and_infrastructure_changes_on_their_qualified_paths(self):
        qualification = dict(version='research-controller-recovery-v1', scientific_and_request_content_unchanged=True, existing_tests_unchanged=True)
        self.assertEqual(releases.plan(BACK, ['research_workflow/worker.py', 'docs/change.md'], qualification), 'research-runtime')
        for paths, proof in [(['research_workflow/worker.py', 'real_world/api.py'], qualification), (['research_workflow/worker.py'], None), (['research_workflow/worker.py'], dict(qualification, existing_tests_unchanged=False))]:
            with self.subTest(paths=paths, proof=proof), self.assertRaises(Hold): releases.plan(BACK, paths, proof)
        for path in ['real_world/api.py', 'openclaw_worker/runtime.py']:
            with self.subTest(path=path), self.assertRaisesRegex(Hold, 'versioned runtime pin'): releases.plan(BACK, [path])
        for path in ['ami/image.py', 'assets/openclaw/service.py', 'agent/app.py']:
            with self.subTest(path=path), self.assertRaisesRegex(Hold, 'dedicated image'): releases.plan(INFRA, [path])
        self.assertEqual(releases.plan(BACK, ['benchmark_ingest.py']), 'backend'); self.assertEqual(releases.plan(FRONT, ['app/page.tsx']), 'frontend'); self.assertEqual(releases.plan(INFRA, ['lib/platform.ts']), 'infrastructure')

    def test_dispatch_reconciles_existing_runs_and_uses_only_frozen_component_specific_inputs(self):
        self.assertEqual(self.broker.dispatch(self.operation), {'dispatched': True}); self.github.post.assert_not_called()
        self.runs = []
        for component, repository in [('frontend', FRONT), ('backend', BACK), ('infrastructure', INFRA), ('research-runtime', BACK), ('supervisor-runtime', BACK)]:
            operation = copy.deepcopy(self.operation); operation['request'].update(component=component, repository=repository, pin_revision='c' * 40, previous_revision='d' * 40, qualification_artifact_id='retained-qualification')
            with self.subTest(component=component):
                self.assertEqual(self.broker.dispatch(operation), {'dispatched': True}); target_repository, path, body = self.github.post.call_args.args
                self.assertEqual(target_repository, BACK if component == 'backend' else INFRA); self.assertEqual(body['ref'], 'main'); self.assertEqual(body['inputs']['operation_id'], operation['id'])
                if component == 'backend': self.assertTrue(path.endswith('deploy.yml/dispatches')); self.assertEqual(body['inputs'], dict(operation_id=operation['id'], expected_revision='a' * 40))
                elif component == 'research-runtime': self.assertIn('research-runtime-release.yml', path); self.assertEqual(body['inputs'], dict(operation_id=operation['id'], revision='a' * 40, pin_revision='c' * 40))
                elif component == 'supervisor-runtime': self.assertIn('supervisor-runtime-release.yml', path); self.assertEqual(body['inputs']['qualification_artifact_id'], 'retained-qualification'); self.assertEqual(body['inputs']['previous_revision'], 'd' * 40)
                else: self.assertEqual(body['inputs'], dict(operation_id=operation['id'], repository=repository, revision='a' * 40, component=component))
        self.github.main.return_value = 'e' * 40; before = self.github.post.call_count
        with self.assertRaisesRegex(Hold, 'main advanced'): self.broker.dispatch(self.operation)
        self.assertEqual(self.github.post.call_count, before)

    def test_pending_missing_and_failed_runs_never_become_verified_releases(self):
        self.runs = []; self.assertIsNone(self.broker.receipt(self.operation))
        self.runs = [dict(self.run, status='in_progress')]; self.assertIsNone(self.broker.receipt(self.operation))
        self.runs = [dict(self.run, conclusion='failure')]
        with self.assertRaises(releases.ReleaseFailure) as caught: self.broker.receipt(self.operation)
        self.assertEqual(caught.exception.receipt, dict(workflow_run_id='23', conclusion='failure', status='failed', production_revision_verified=False)); self.github.opener.open.assert_not_called()

    def test_success_receipt_binds_every_original_identity_and_retains_unknown_cost_or_rollback_fields(self):
        self.proof.update(measured_charges_usd=None, rollback_status=None)
        result = self.broker.receipt(self.operation); self.assertEqual(result, dict(self.proof, workflow_run_id='23', workflow_revision='b' * 40))
        request = self.github.opener.open.call_args.args[0]; self.assertEqual(request.full_url, 'https://api.github.com/repos/' + INFRA + '/actions/artifacts/42/zip'); self.assertEqual(request.get_header('Authorization'), 'Bearer synthetic-header-only-token')
        for field, value in [('passed', 1), ('revision', 'changed'), ('repository', BACK), ('operation_id', 'other-operation'), ('component', 'backend')]:
            original = copy.deepcopy(self.proof); self.proof[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(Hold, 'does not bind'): self.broker.receipt(self.operation)
            self.proof = original

    def test_receipt_layout_inventory_and_download_bounds_are_required_before_accepting_proof(self):
        original = copy.deepcopy(self.artifacts)
        for artifacts in [[], [dict(original[0], expired=True)], original * 2, [dict(original[0], size_in_bytes=5_000_001)]]:
            self.artifacts = artifacts
            with self.subTest(artifacts=artifacts), self.assertRaises(Hold): self.broker.receipt(self.operation)
        self.artifacts = original
        for raw in [b'x' * 5_000_001, archive({'unexpected.json': canonical(self.proof)}), archive({'production-verification.json': b' ' * 100001}), archive({'production-verification.json': canonical(self.proof), 'extra.txt': b'Unapproved member'})]:
            self.github.opener.open.side_effect = lambda request, raw=raw, **options: io.BytesIO(raw)
            with self.subTest(bytes=len(raw)), self.assertRaises(Hold): self.broker.receipt(self.operation)

    def test_signed_object_redirect_strips_the_publisher_credential_and_rejects_unexpected_hosts(self):
        self.github.token = MagicMock(return_value='synthetic-rotating-token')
        raw = archive({'production-verification.json': canonical(self.proof)})
        for url in ['https://results.blob.core.windows.net/retained?signature=synthetic', 'https://results.githubusercontent.com/retained?signature=synthetic']:
            self.github.opener.open.side_effect = [urllib.error.HTTPError('https://api.github.com/private', 302, 'Found', {'Location': url}, None), io.BytesIO(raw)]
            self.assertTrue(self.broker.receipt(self.operation)['passed']); first, second = self.github.opener.open.call_args_list[-2:]
            self.assertEqual(first.args[0].get_header('Authorization'), 'Bearer synthetic-rotating-token'); self.assertIsNone(second.args[0].get_header('Authorization')); self.assertEqual(second.args[0].full_url, url)
        for url in ['http://results.blob.core.windows.net/object', 'https://unrelated.example.test/object', 'https://user:secret@results.githubusercontent.com/object', 'https:///missing-host', '']:
            self.github.opener.open.side_effect = urllib.error.HTTPError('https://api.github.com/private', 302, 'Found', {'Location': url}, None)
            with self.subTest(url=url), self.assertRaisesRegex(Hold, 'Unexpected release artifact host'): self.broker.receipt(self.operation)
        self.github.opener.open.side_effect = urllib.error.HTTPError('https://api.github.com/private', 503, 'PRIVATE', {}, None)
        with self.assertRaisesRegex(Hold, 'download is unconfirmed') as caught: self.broker.receipt(self.operation)
        self.assertEqual(caught.exception.status, 'reconciling'); self.assertNotIn('PRIVATE', str(caught.exception))


if __name__ == '__main__': unittest.main()
