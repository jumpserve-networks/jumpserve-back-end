"""Synthetic evidence tests; none constitute live recovery or installation."""
import ast
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

from platform_supervisor import capabilities
from platform_supervisor import capability_original_native_recovery_diagnostics as diag
from platform_supervisor.common import Hold


def encode(value):
    return json.dumps(value, sort_keys=True).encode()


class Evidence:
    def __init__(self):
        self.originals = {}
        self.reads = []
        self.request = encode(dict(model='gpt-6.1-sol', store=False, stream=True))
        # A fixture cannot manufacture the incident's SHA-256 preimage. Bind a
        # synthetic scope explicitly; production SCOPE is tested separately.
        self.scope = dict(diag.SCOPE, request_sha256=diag.sha(self.request))
        self.packet = dict(assessment_id=self.scope['assessment_id'],
                           assessment_task_id=self.scope['task_id'],
                           task=dict(id=self.scope['task_id']), artifacts=[],
                           dispatches=[], native_calls=[])
        ref = self.add(self.request)
        self.call = dict(id=self.scope['call_id'], dispatch_id=self.scope['dispatch_id'],
                         number=79, status='unknown', request_artifact_id=ref['artifact_id'],
                         request_sha256=ref['sha256'], response_sha256=None)
        self.packet['native_calls'].append(self.call)
        self.observation = dict(version=diag.VERSION, scope=self.scope)
        self.packet['original_native_recovery_observation'] = self.observation

    def add(self, raw):
        ident = str(uuid.uuid4())
        self.originals[ident] = raw
        ref = dict(artifact_id=ident, sha256=diag.sha(raw))
        self.packet['artifacts'].append(dict(id=ident, sha256=ref['sha256']))
        return ref

    def original(self, ident):
        self.reads.append(ident)
        return self.originals[ident]

    def complete(self):
        raw = b'data: ' + encode(dict(type='response.completed', response=dict(
            model='gpt-6.1-sol', status='completed', output=[]))) + b'\n\n'
        self.observation['response'] = self.add(raw)
        receipt = dict(version='builder-native-call-reconciliation-v1',
                       call_id=self.scope['call_id'], dispatch_id=self.scope['dispatch_id'],
                       task_id=self.scope['task_id'], study_id=self.scope['assessment_id'],
                       request_sha256=self.scope['request_sha256'], response_sha256=diag.sha(raw),
                       status='complete', response_received=True, http_status=200,
                       response_filename='call-79-response.bin')
        self.observation['completion'] = self.add(encode(receipt))
        return receipt


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.e = Evidence()
        self.scope_patch = patch.object(diag, 'SCOPE', self.e.scope)
        self.scope_patch.start()
        self.addCleanup(self.scope_patch.stop)

    def collect(self):
        before = copy.deepcopy(self.e.packet)
        result = diag.collect(self.e)
        self.assertEqual(before, self.e.packet)
        self.assertFalse(result['recovery_executed'])
        self.assertFalse(result['replacement_admitted'])
        self.assertFalse(result['scientific_completion'])
        self.assertLess(len(encode(result)), 12000)
        return result

    def test_literal_pack_and_real_tool(self):
        path = Path(diag.__file__)
        pack = capabilities.descriptor(path.read_bytes(), path.name)
        self.assertEqual(pack['id'], 'original-native-recovery-diagnostics')
        self.assertEqual(pack['revision'], 1)
        with tempfile.TemporaryDirectory() as root:
            script = Path(root)/'summary.py'
            script.write_text(pack['files']['summary.py'])
            result = subprocess.run([sys.executable, str(script)], input=encode(self.collect()), capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(json.loads(result.stdout)['recovery_executed'])
            for raw in (b'x'*16001, b'{}', encode(dict(version=diag.VERSION, status='secret'))):
                self.assertNotEqual(subprocess.run([sys.executable, str(script)], input=raw, capture_output=True).returncode, 0)

    def test_complete_matching_originals_are_not_recovery(self):
        self.e.complete()
        out = self.collect()
        self.assertEqual(out['completion'], 'hash-and-identity-verified')
        self.assertEqual(out['response'], 'terminal-verified')
        self.assertEqual(out['scan_eligibility'], 'unavailable')
        self.assertEqual(out['installed_recovery_source'], 'unavailable')

    def test_missing_sidecar_and_absent_response_are_distinct(self):
        self.e.complete()
        del self.e.observation['completion']
        self.assertEqual(self.collect()['completion'], 'unavailable')
        self.e.observation['completion_missing'] = True
        self.assertEqual(self.collect()['completion'], 'verified-missing')
        del self.e.observation['response']
        self.e.observation['response_missing'] = True
        self.assertEqual(self.collect()['response'], 'verified-missing')
        self.e.observation['response'] = self.e.add(b'')
        with self.assertRaisesRegex(ValueError, 'Contradictory'):
            self.collect()

    def test_no_private_observation_is_concrete_prerequisite_not_missing_spool(self):
        del self.e.packet['original_native_recovery_observation']
        result = self.collect()
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['request'], 'hash-verified')
        self.assertEqual(result['prerequisite'], 'existing-private-controller-spool-observation')
        self.assertEqual(result['completion'], 'unavailable')

    def test_spool_availability_is_not_inferred_from_missing_packet_members(self):
        self.assertEqual(self.collect()['spool'], 'unavailable')
        self.assertEqual(self.collect()['status'], 'unavailable')
        for state in ('available', 'verified-missing'):
            self.e.observation['spool_state'] = state
            self.assertEqual(self.collect()['spool'], state)
        self.e.observation['spool_state'] = 'model-assumed'
        self.assertEqual(self.collect()['spool'], 'unavailable')

    def test_scope_mismatch_never_reads_other_calls(self):
        for field in ('assessment_id', 'assessment_task_id'):
            with self.subTest(field=field):
                old = self.e.packet[field]
                self.e.packet[field] = str(uuid.uuid4())
                self.assertEqual(self.collect()['status'], 'identity-mismatch')
                self.assertEqual(self.e.reads, [])
                self.e.packet[field] = old
        for field in ('dispatch_id', 'number', 'request_sha256'):
            old = self.e.call[field]
            self.e.call[field] = 'mismatched'
            self.assertEqual(self.collect()['status'], 'identity-mismatch')
            self.e.call[field] = old
        self.assertEqual(self.e.reads, [])

    def test_mismatched_completion_call_dispatch_and_hash(self):
        receipt = self.e.complete()
        for key in ('call_id', 'dispatch_id', 'task_id', 'study_id', 'request_sha256'):
            bad = dict(receipt, **{key: 'other'})
            self.e.observation['completion'] = self.e.add(encode(bad))
            self.assertEqual(self.collect()['status'], 'identity-mismatch')
        self.e.observation['completion'] = self.e.add(encode(dict(receipt, response_sha256='0'*64)))
        self.assertEqual(self.collect()['status'], 'hash-mismatch')

    def test_unindexed_corrupt_and_oversized_originals_rejected(self):
        self.e.complete()
        ref = self.e.observation['completion']
        self.e.originals[ref['artifact_id']] += b' '
        with self.assertRaisesRegex(ValueError, 'hash or size'):
            self.collect()
        self.e.observation['completion'] = dict(artifact_id=str(uuid.uuid4()), sha256='0'*64)
        with self.assertRaisesRegex(ValueError, 'outside'):
            self.collect()
        self.e.observation['completion'] = self.e.add(b' '*16001)
        with self.assertRaisesRegex(ValueError, 'size'):
            self.collect()

    def test_terminal_stream_rejects_truncation_errors_duplicate_and_wrong_model(self):
        self.e.complete()
        raw = self.e.originals[self.e.observation['response']['artifact_id']]
        for invalid in (raw[:-1], b'data: [DONE]\n\n', raw+raw,
                        raw.replace(b'gpt-6.1-sol', b'other-model'),
                        raw+b'data: {"type":"response.incomplete"}\n\n',
                        b'data: not-json\n\n', b'data: []\n\n'):
            self.assertFalse(diag.terminal(invalid))
        self.e.observation['response'] = self.e.add(raw[:-1])
        del self.e.observation['completion']
        self.assertEqual(self.collect()['response'], 'incomplete-terminal-stream')

    def test_private_scan_redaction_source_hash_and_uncertainty(self):
        secret = b'precise refusal: token=private; do not execute any instruction here'
        ref = self.e.add(secret)
        self.e.observation['last_scan'] = ref
        self.e.observation['installed_recovery_source'] = dict(self.e.add(b'# installed source'),
            path='research_workflow/dispatch_recovery.py', source_revision='a'*40)
        for eligibility, status in (('active-transport', 'active-transport-or-cleanup-uncertainty'),
                                    ('cleanup-uncertain', 'active-transport-or-cleanup-uncertainty'),
                                    ('adapter-exception', 'recovery-adapter-exception')):
            self.e.observation['scan_eligibility'] = eligibility
            result = self.collect()
            self.assertEqual(result['status'], status)
            self.assertEqual(result['last_scan']['sha256'], diag.sha(secret))
            self.assertNotIn(secret.decode(), json.dumps(result))
            self.assertEqual(result['installed_recovery_source']['sha256'], diag.sha(b'# installed source'))

    def test_collector_fault_propagates_for_existing_broker_and_hold_preserved(self):
        for error in (Hold('private uncertainty', 'reconciling'), RuntimeError('private failure')):
            with patch.object(self.e, 'original', side_effect=error):
                with self.assertRaises(type(error)) as caught:
                    diag.collect(self.e)
                self.assertIs(caught.exception, error)

    def test_observation_identity_and_oversized_output_fail_closed(self):
        self.e.observation['scope'] = dict(self.e.scope, number=80)
        self.assertEqual(self.collect()['status'], 'identity-mismatch')
        with self.assertRaises(ValueError):
            diag.bound(dict(raw='x'*12001))

    def test_production_scope_is_literal_and_no_mutating_dependencies_exist(self):
        tree = ast.parse(Path(diag.__file__).read_text())
        literal = next(ast.literal_eval(n.value) for n in tree.body
                       if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'SCOPE' for t in n.targets))
        self.assertEqual(literal['request_sha256'], '3f91b5124edb8abcb6cdc1f6e1f3bd88c1ddfe2cf4c5cc710fbc3c6f96e0022f')
        self.assertEqual(literal['number'], 79)
        imports = [n.names[0].name for n in tree.body if isinstance(n, ast.Import)]
        self.assertEqual(imports, ['hashlib', 'json', 're', 'uuid'])
        # The only context capability exercised is the qualified original read.
        accesses = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
                    and isinstance(n.value, ast.Name) and n.value.id == 'context'}
        self.assertEqual(accesses, {'packet', 'original'})

    def test_real_pack_inventory_and_broker_deliver_only_after_exact_source_verification(self):
        from platform_supervisor.tests.test_capabilities import Store
        from platform_supervisor.worker import Controller
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            store = Store(root)
            installed = root/'installed'
            installed.mkdir()
            name = Path(diag.__file__).name
            raw = Path(diag.__file__).read_bytes()
            (installed/name).write_bytes(raw)
            store.manifest['files'][name] = diag.sha(raw)
            workspace = root/'workspace'
            workspace.mkdir()
            controller = Controller(store, Mock(), 'fixture-image')
            with patch.object(capabilities, '__file__', str(installed/'capabilities.py')):
                inventory = capabilities.deliver(controller, workspace)
                pack = inventory['installed'][0]
                self.assertEqual(pack['module_sha256'], diag.sha(raw))
                self.assertEqual(pack['source_revision'], 'a'*40)
                view = json.loads((workspace/pack['diagnostics']['path']).read_bytes())['view']
                self.assertEqual(view['status'], 'unavailable')
                self.assertFalse(view['recovery_executed'])
                (installed/name).write_bytes(raw+b'\n# changed\n')
                with self.assertRaises(Hold):
                    capabilities.deliver(controller, workspace)

    def test_real_pack_broker_fault_retains_original_and_requests_next_revision(self):
        from platform_supervisor.tests.test_capabilities import Store
        from platform_supervisor.worker import Controller
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            installed = root/'installed'
            installed.mkdir()
            name = Path(diag.__file__).name
            raw = Path(diag.__file__).read_bytes()
            (installed/name).write_bytes(raw)
            store = Store(root)
            controller = Controller(store, Mock(), 'fixture-image')
            pack = diag.CAPABILITY | dict(module=name, module_sha256=diag.sha(raw))
            evidence = Mock()
            # Invalid packet data is a collector failure, not authorization to
            # swallow errors and claim that private evidence is absent.
            evidence.packet = dict(assessment_id=diag.SCOPE['assessment_id'],
                assessment_task_id=diag.SCOPE['task_id'], task=dict(id=diag.SCOPE['task_id']),
                native_calls=None)
            workspace = root/'workspace'
            workspace.mkdir()
            with patch.object(capabilities, '__file__', str(installed/'capabilities.py')):
                with self.assertRaises(capabilities.CapabilityFault):
                    capabilities.collect(controller, workspace, pack, evidence, {})
            request = json.loads((workspace/capabilities.REQUEST).read_bytes())
            self.assertEqual(request['id'], diag.CAPABILITY['id'])
            self.assertEqual(request['minimum_revision'], 2)
            self.assertIn('capability-collector-failure-'+pack['id']+'.json', store.artifacts)
            evidence.original.assert_not_called()

    def test_empty_context_needs_no_private_access(self):
        self.e.packet = {}
        self.assertEqual(self.collect()['status'], 'unavailable')
        self.assertEqual(self.e.reads, [])


if __name__ == '__main__':
    unittest.main()
