"""Capability installation uncertainty, original evidence and source boundaries."""
import json
import unittest
from unittest.mock import patch
import uuid

from platform_supervisor import capabilities
from platform_supervisor.common import canonical, digest, Hold
import test_capabilities as fixtures


class CapabilityBoundaries(unittest.TestCase):
    setUp = fixtures.Capabilities.setUp
    install_fixture = fixtures.Capabilities.install_fixture

    def workspace(self):
        path = self.root / 'workspace'; path.mkdir(exist_ok=True); return path

    def test_request_and_module_identifiers_reject_malformed_types_bounds_and_reserved_paths(self):
        self.assertEqual(capabilities.validate_request(fixtures.specification())['id'], 'json-count')
        for value in [None, [], {}, dict(fixtures.specification(), minimum_revision=True), dict(fixtures.specification(), acceptance=['']), dict(fixtures.specification(), objective='')]:
            with self.subTest(value=value), self.assertRaises(ValueError): capabilities.validate_request(value)
        for value in [None, 'UPPER', '../path', 'a', 'x' * 64]:
            with self.subTest(value=value), self.assertRaises(ValueError): capabilities.module_name(value)
        self.assertEqual(capabilities.module_name('json-count'), 'capability_json_count.py')

    def test_literal_metadata_rejects_multiple_assignments_nonstring_names_and_oversized_packs_without_executing_code(self):
        for raw in [b'CAPABILITY = other = {}', b'no_contract = True', b'CAPABILITY = {}\nCAPABILITY = {}']:
            with self.subTest(raw=raw), self.assertRaises(ValueError): capabilities.descriptor(raw, 'capability_json_count.py')
        for files in [{1: 'nonstring'}, {'tool.py': 'x' * 256001}, {str(i) + '.txt': 'x' * 250001 for i in range(4)}]:
            value = dict(fixtures.pack(), files=files)
            with self.subTest(files=list(files)), self.assertRaises(ValueError): capabilities.descriptor(('CAPABILITY=' + repr(value)).encode(), 'capability_json_count.py')
        value = fixtures.pack(); raw = ('CAPABILITY=' + repr(value) + '\nraise RuntimeError("candidate statements must not execute")').encode()
        self.assertEqual(capabilities.descriptor(raw, 'capability_json_count.py'), value)

    def test_task_evidence_propagates_uncertainty_and_requires_exact_pointer_size_and_original_hash(self):
        ident, study = str(uuid.uuid4()), str(uuid.uuid4()); raw = canonical({'zero': 0, 'missing': None}); sha = digest(raw)
        packet = dict(assessment_id=study, artifacts=[dict(id=ident, sha256=sha)]); row = dict(id=ident, sha256=sha, byte_count=len(raw), storage_path=study + '/' + sha)
        self.store.originals[ident] = (row, raw); reader = capabilities.Evidence(self.store, packet); self.assertEqual(reader.original(ident), raw)
        for change in [dict(storage_path='different-identity'), dict(byte_count=True), dict(byte_count=1048577), dict(sha256='0' * 64)]:
            with self.subTest(change=change):
                self.store.originals[ident] = (dict(row, **change), raw)
                with self.assertRaises((Hold, ValueError)): reader.original(ident)
        for error in [OSError('Original read unconfirmed'), ValueError('Malformed persistence response'), Hold('Original identity remains uncertain', 'reconciling')]:
            with self.subTest(error=type(error).__name__), patch.object(self.store, 'request', side_effect=error), self.assertRaises(Hold) as caught: reader.original(ident)
            if isinstance(error, Hold): self.assertIs(caught.exception, error)
        ambiguous = dict(packet, dispatches=[dict(request_artifact_id=ident, request_sha256='0' * 64)])
        with self.assertRaisesRegex(ValueError, 'Ambiguous original'): capabilities.Evidence(self.store, ambiguous)

    def test_inventory_requires_verified_manifest_and_bounded_plain_installed_source(self):
        name, raw = self.install_fixture(); self.store.manifest['files']['unrelated.py'] = '0' * 64
        self.assertEqual(len(capabilities.inventory(self.store, self.installed)), 1)
        for manifest in [None, {'files': []}]:
            with self.subTest(manifest=manifest), patch.object(self.store, 'request', return_value=manifest), self.assertRaisesRegex(Hold, 'manifest is unavailable'): capabilities.inventory(self.store, self.installed)
        path = self.installed / name; path.unlink()
        with self.assertRaisesRegex(Hold, 'missing or unsafe'): capabilities.inventory(self.store, self.installed)
        path.write_bytes(b'x' * 1500001)
        with self.assertRaisesRegex(Hold, 'missing or unsafe'): capabilities.inventory(self.store, self.installed)
        path.unlink(); original = self.installed / 'original.txt'; original.write_bytes(raw); path.symlink_to(original)
        with self.assertRaisesRegex(Hold, 'missing or unsafe'): capabilities.inventory(self.store, self.installed)

    def test_delivery_rejects_changed_tools_and_linked_parent_paths_before_writing_the_index(self):
        self.install_fixture(); workspace = self.workspace()
        with patch.object(capabilities, '__file__', str(self.installed / 'capabilities.py')):
            value = capabilities.deliver(self.controller, workspace); capabilities.deliver(self.controller, workspace)
            path = workspace / value['installed'][0]['files'][0]['path']; original = path.read_bytes(); path.write_bytes(original + b'changed')
            with self.assertRaisesRegex(Hold, 'was modified'): capabilities.deliver(self.controller, workspace)
            path.unlink(); path.symlink_to(self.installed / 'retained-source.txt')
            with self.assertRaisesRegex(Hold, 'contains a link'): capabilities.deliver(self.controller, workspace)

    def test_collectors_require_task_binding_and_do_not_turn_transient_original_read_uncertainty_into_an_upgrade(self):
        name, raw = self.install_fixture(); raw += b'\nfrom .common import Hold\ndef collect(context):\n raise Hold("Retained persistence uncertainty", "reconciling")\n'; (self.installed / name).write_bytes(raw); self.store.manifest['files'][name] = digest(raw); workspace = self.workspace(); self.store.task['assessment_repair'] = True
        self.store.packet = {'platform_task_id': str(uuid.uuid4())}
        with patch.object(capabilities, '__file__', str(self.installed / 'capabilities.py')):
            with self.assertRaisesRegex(Hold, 'not task-bound'): capabilities.deliver(self.controller, workspace)
            self.store.packet = dict(platform_task_id=self.store.task['id'])
            with self.assertRaisesRegex(Hold, 'Retained persistence uncertainty'): capabilities.deliver(self.controller, workspace)
            pack = capabilities.inventory(self.store)[0]; (self.installed / name).write_bytes(raw + b'changed')
            with self.assertRaisesRegex(Hold, 'changed before invocation'): capabilities.collect(self.controller, workspace, pack, capabilities.Evidence(self.store, {}), {})
        self.assertEqual(self.store.artifacts, {}); self.assertFalse((workspace / capabilities.REQUEST).exists())

    def test_collector_fault_retains_an_existing_request_and_requests_a_new_revision_for_nonobject_output(self):
        name, raw = self.install_fixture(); raw += b'\ndef collect(context):\n return ["not a diagnostic object"]\n'; (self.installed / name).write_bytes(raw); self.store.manifest['files'][name] = digest(raw); workspace = self.workspace(); original = canonical(fixtures.specification()); (workspace / capabilities.REQUEST).write_bytes(original)
        with patch.object(capabilities, '__file__', str(self.installed / 'capabilities.py')), self.assertRaises(capabilities.CapabilityFault): capabilities.deliver(self.controller, workspace)
        self.assertEqual(self.store.artifacts[capabilities.REQUEST][0], original); self.assertEqual(json.loads((workspace / capabilities.REQUEST).read_bytes())['minimum_revision'], 2)

    def test_request_rejects_unsafe_or_invalid_files_retains_originals_and_handles_installed_or_removed_capabilities(self):
        workspace = self.workspace(); path = workspace / capabilities.REQUEST
        self.assertIsNone(capabilities.request(self.controller, workspace)); path.write_bytes(b'not JSON')
        with self.assertRaisesRegex(Hold, 'Correct capability-request'): capabilities.request(self.controller, workspace)
        path.write_bytes(b'x' * 16001)
        with self.assertRaisesRegex(Hold, 'bounded plain JSON'): capabilities.request(self.controller, workspace)
        raw = canonical(fixtures.specification()); path.write_bytes(raw)
        with patch.object(self.store, 'request', return_value=dict(activation_lost=True, minimum_revision=2)), self.assertRaisesRegex(Hold, 'removed by a later runtime'): capabilities.request(self.controller, workspace)
        self.assertEqual(self.store.artifacts[capabilities.REQUEST][0], raw); self.assertEqual(json.loads(path.read_bytes())['minimum_revision'], 2)
        self.controller.state['repair_guard'] = {'retained': True}
        with patch.object(self.store, 'request', return_value=dict(available=True)), self.assertRaisesRegex(Hold, 'Verified capability is available'): capabilities.request(self.controller, workspace)
        self.assertFalse(path.exists()); self.assertNotIn('repair_guard', self.controller.state); self.assertIn('Do not request it again', self.controller.state['feedback'])

    def test_candidate_gate_requires_sufficient_revision_literal_contract_and_a_new_discoverable_assertion(self):
        workspace = self.workspace(); context = dict(id=str(uuid.uuid4()), spec=dict(fixtures.specification(), minimum_revision=2)); self.controller.state['capability_implementation'] = context
        folder = workspace / 'platform_supervisor'; tests = folder / 'tests'; tests.mkdir(parents=True); path = folder / capabilities.module_name('json-count'); path.write_text('CAPABILITY={}')
        with self.assertRaisesRegex(Hold, 'Correct the declared'): capabilities.validate_candidate(self.controller, workspace)
        path.write_text('CAPABILITY=' + repr(fixtures.pack()))
        with self.assertRaisesRegex(Hold, 'revision does not satisfy'): capabilities.validate_candidate(self.controller, workspace)
        path.write_text('CAPABILITY=' + repr(dict(fixtures.pack(), revision=2)))
        (tests / 'test_invalid.py').write_text('not valid Python !!!'); (tests / 'test_decorated.py').write_text('from unittest import TestCase\nclass Example(TestCase):\n @skip\n def test_skipped(self):\n  assert True\n'); (tests / 'test_existing.py').write_text('from unittest import TestCase\nclass Example(TestCase):\n def test_old(self):\n  assert True\n')
        self.controller.state['sources'] = {capabilities.BACKEND: {'snapshot': {'platform_supervisor/tests/test_existing.py': 'retained'}}}
        with self.assertRaisesRegex(Hold, 'new discoverable regression'): capabilities.validate_candidate(self.controller, workspace)
        (tests / 'test_new.py').write_text('from unittest import TestCase\nclass Example(TestCase):\n def test_new(self):\n  assert 2 + 2 == 4\n'); capabilities.validate_candidate(self.controller, workspace)
        installed = [dict(id='json-count', revision=2, module_sha256='b' * 64, source_revision='c' * 40)]
        with patch.object(capabilities, 'inventory', return_value=installed): self.assertTrue(capabilities.activate(self.controller)['available'])
        request = self.store.calls[-1]; self.assertEqual(request['p_operation'], 'activate'); self.assertEqual(request['p_payload']['request_id'], context['id']); self.assertEqual(request['p_payload']['module_sha256'], 'b' * 64)
        self.controller.state.pop('capability_implementation'); self.assertIsNone(capabilities.activate(self.controller)); self.assertIsNone(capabilities.validate_candidate(self.controller, workspace))

    def resume(self, workspace, installed, rows=None):
        activation = str(uuid.uuid4()); self.controller.state['capability_activation'] = activation
        def read(path, *args, **options):
            if '/platform_capability_requests?' in path: return rows if rows is not None else [dict(id=activation, capability_id='json-count', minimum_revision=1)]
            if '/platform_capability_activations?' in path: return [dict(module_sha256='b' * 64, revision=1)]
            raise AssertionError(path)
        with patch.object(self.store, 'request', side_effect=read): capabilities.resume_progress(self.controller, workspace, installed)

    def test_resume_requires_a_unique_activation_and_preserves_invalid_unrelated_or_newer_local_requests(self):
        workspace = self.workspace(); installed = [dict(id='json-count', revision=1, module_sha256='b' * 64)]; path = workspace / capabilities.REQUEST
        self.resume(workspace, installed, rows=[]); self.assertNotIn('capability_progress_restored', self.controller.state)
        for raw in [b'not JSON', canonical(dict(fixtures.specification(), id='other-tool')), canonical(dict(fixtures.specification(), minimum_revision=2))]:
            with self.subTest(raw=raw):
                path.write_bytes(raw); self.resume(workspace, installed); self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(self.store.artifacts, {})

    def test_resume_preserves_unsafe_old_progress_for_reconciliation(self):
        workspace = self.workspace(); installed = [dict(id='json-count', revision=1, module_sha256='b' * 64)]; path = workspace / 'progress.json'; path.write_bytes(b'x' * 32001)
        with self.assertRaisesRegex(Hold, 'stop is unsafe'): self.resume(workspace, installed)
        self.assertEqual(path.stat().st_size, 32001); self.assertEqual(self.store.artifacts, {})


if __name__ == '__main__': unittest.main()
