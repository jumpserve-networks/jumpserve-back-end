"""Builder source identities, workspace limits and lifecycle scope."""
import contextlib
import copy
import gzip
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import uuid
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import experiment_builder as builder
import generated_package
import store
from workflow import canonical, digest
from test_experiment_builder import project
from test_network_traffic_allowance import built_protocol

def uid(): return str(uuid.uuid4())


class BuilderBoundaries(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='builder-boundary-unit-'); self.addCleanup(temp.cleanup); self.root = Path(temp.name)

    def test_only_the_exact_corrected_versioned_vendor_package_can_start(self):
        package = self.root / 'package'; (package / 'dist').mkdir(parents=True); raw = b'Synthetic corrected vendor file'; (package / 'dist/docker.mjs').write_bytes(raw)
        metadata = self.root / 'patch.json'; metadata.write_bytes(canonical(dict(upstream_version='fixture-1', path='dist/docker.mjs', corrected_sha256=digest(raw))))
        (package / 'package.json').write_bytes(b'{"version":"fixture-1"}')
        with patch.object(builder, 'PATCH_PATH', metadata), patch.dict(os.environ, {'OPENCLAW_BUILDER_PACKAGE': str(package)}):
            self.assertEqual(builder.builder_package(), package)
            (package / 'dist/docker.mjs').write_bytes(b'changed');
            with self.assertRaisesRegex(ValueError, 'correction identity'): builder.builder_package()
            (package / 'package.json').write_bytes(b'{"version":"other"}')
            with self.assertRaisesRegex(ValueError, 'correction identity'): builder.builder_package()
        with patch.dict(os.environ, {'OPENCLAW_BUILDER_PACKAGE': 'relative-package'}), self.assertRaisesRegex(ValueError, 'versioned builder-only'): builder.builder_package()

    def test_normalization_command_has_one_workspace_mount_and_no_network_or_writable_host_root(self):
        image = 'registry.example.test/image@sha256:' + 'a' * 64
        with patch.object(builder.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'{"bytes_unchanged":true}', b'')) as run:
            self.assertEqual(builder.normalize_workspace(self.root, image), b'{"bytes_unchanged":true}')
            command = run.call_args.args[0]; self.assertIn('--read-only', command); self.assertEqual(command[command.index('--network') + 1], 'none')
            self.assertEqual(command.count('--mount'), 1); self.assertEqual(command[command.index('--mount') + 1], 'type=bind,src=' + str(self.root) + ',dst=/workspace')
            self.assertEqual(command[command.index('--user') + 1], '0:0'); self.assertIn('no-new-privileges', command); self.assertNotIn('/var/run/docker.sock', ' '.join(command))
            run.return_value.stdout = b'{"bytes_unchanged":false}'
            with self.assertRaisesRegex(ValueError, 'byte verification'): builder.normalize_workspace(self.root, image)
            for path, target in [(Path('relative'), image), (self.root / 'unsafe,source', image), (self.root, 'python:latest')]:
                with self.subTest(path=path, image=target), self.assertRaises(ValueError): builder.normalize_workspace(path, target)
            link = self.root / 'link'; link.symlink_to(self.root, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'Unsafe workspace'): builder.normalize_workspace(link, image)

    def test_embedded_normalizer_preserves_actual_bytes_and_executable_modes_and_rejects_special_files(self):
        workspace = self.root / 'workspace'; workspace.mkdir(); (workspace / 'source.py').write_bytes(b'print("original")\n'); executable = workspace / 'run.sh'; executable.write_bytes(b'#!/bin/sh\nexit 0\n'); executable.chmod(0o755)
        code = builder.NORMALIZE_WORKSPACE.replace("root='/workspace'", 'root=' + repr(str(workspace)))
        output = io.StringIO()
        with patch.object(sys, 'argv', ['normalizer', str(os.getuid()), str(os.getgid())]), patch.object(os, 'chown') as chown, contextlib.redirect_stdout(output): exec(compile(code, '<workspace-normalizer>', 'exec'), {})
        receipt = json.loads(output.getvalue()); self.assertTrue(receipt['bytes_unchanged']); self.assertEqual(len(receipt['files']), 2); self.assertEqual(chown.call_count, 2)
        for record in receipt['files']: self.assertEqual(digest((workspace / record['path']).read_bytes()), record['sha256'])
        self.assertEqual(executable.stat().st_mode & 0o777, 0o700); self.assertEqual((workspace / 'source.py').stat().st_mode & 0o777, 0o600)
        (workspace / 'unsafe').symlink_to(executable)
        with patch.object(sys, 'argv', ['normalizer', '1', '1']), patch.object(os, 'chown'), contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, 'Unsupported workspace file type'): exec(compile(code, '<workspace-normalizer>', 'exec'), {})

    def test_container_lifecycle_is_scoped_by_exact_task_uuid_and_validated_container_ids(self):
        task = uid(); identifier = 'a' * 12; commands = []; state = {'Running': False, 'Paused': True}
        def run(command, **options):
            commands.append(command); self.assertTrue(options['check'])
            if command[1] == 'ps': return subprocess.CompletedProcess(command, 0, (identifier + '\n').encode(), b'')
            if command[1] == 'inspect': return subprocess.CompletedProcess(command, 0, canonical(state), b'')
            return subprocess.CompletedProcess(command, 0, b'', b'')
        with patch.object(builder.subprocess, 'run', side_effect=run):
            self.assertEqual(builder.container_action(task, 'list'), [identifier]); self.assertEqual(len(commands), 1); self.assertIn('name=^/js-build-' + task.replace('-', '') + '-', commands[0])
            for action, running, expected in [('start', False, 'start'), ('start', True, None), ('stop', True, 'stop'), ('stop', False, None), ('remove', False, 'rm')]:
                state['Running'] = running; commands.clear(); builder.container_action(task, action)
                self.assertIn(['docker', 'unpause', identifier], commands); self.assertEqual(any(c[1] in ['start', 'stop', 'rm'] for c in commands), expected is not None)
                if expected: self.assertTrue(any(c[1] == expected and c[-1] == identifier for c in commands))
            commands.clear()
            with self.assertRaises(ValueError): builder.container_action(task, 'arbitrary')
            with self.assertRaises(ValueError): builder.container_action('not-a-task', 'remove')
            self.assertFalse(commands)
        with patch.object(builder.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'not-a-container', b'')), self.assertRaisesRegex(ValueError, 'container identity'): builder.container_action(task, 'remove')

    def test_history_transport_bounds_and_indexes_reject_changed_or_incomplete_originals(self):
        history = [{'type': 'function_call_output', 'call_id': 'original', 'output': 'x' * 600000}]; builder.save_history(self.root, history)
        index = json.loads((self.root / 'native-tool-history.json').read_bytes()); self.assertEqual(builder.load_history(self.root), history)
        for change, message in [(dict(version='unknown'), 'Unknown'), (dict(path='other.gz'), 'Unknown'), (dict(compressed_sha256='0' * 64), 'Compressed'), (dict(bytes=index['bytes'] + 1), 'Expanded'), (dict(sha256='0' * 64), 'Expanded'), (dict(records=2), 'record coverage')]:
            (self.root / 'native-tool-history.json').write_bytes(canonical(dict(index, **change)))
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, message): builder.load_history(self.root)
        (self.root / 'native-tool-history.json').write_bytes(canonical(index)); archive = self.root / index['path']; archive.unlink(); archive.symlink_to(self.root / 'untrusted.gz')
        with self.assertRaisesRegex(ValueError, 'symlink'): builder.load_history(self.root)
        with patch.object(builder.gzip, 'compress', return_value=b'x' * 1000001), self.assertRaisesRegex(ValueError, 'Compressed native history checkpoint bound'): builder.history_files(history)
        with self.assertRaisesRegex(ValueError, 'Expanded native history bound'): builder.history_files([{'output': 'x' * builder.HISTORY_BYTES}])
        (self.root / 'native-tool-history.json').unlink(); (self.root / 'native-tool-history.json').symlink_to(self.root / 'different')
        with self.assertRaisesRegex(ValueError, 'symlink'): builder.save_history(self.root, [])

    def test_lossless_history_cannot_decode_to_a_nonlist_even_with_consistent_transport_hashes(self):
        raw = canonical({'not': 'native history'}); compressed = gzip.compress(raw, mtime=0); (self.root / 'native-tool-history.json.gz').write_bytes(compressed)
        (self.root / 'native-tool-history.json').write_bytes(canonical(dict(version='builder-history-gzip-v1', path='native-tool-history.json.gz', compressed_sha256=digest(compressed), sha256=digest(raw), bytes=len(raw), records=1)))
        with self.assertRaisesRegex(ValueError, 'record coverage'): builder.load_history(self.root)

    def test_utf8_page_delivery_is_verified_against_the_original_and_malformed_native_outputs_are_not_counted(self):
        context = {'source_material': 'α' * 4000}; logical = dict(system=[{'text': 'Synthetic instruction'}], builder={'context': context})
        workspace = self.root / 'workspace'; workspace.mkdir(); builder.bootstrap(logical, workspace, self.root, [])
        # Run the actual credential-free reader. A multibyte token crosses the
        # first byte window, so concatenation must reconstruct the original.
        history = []; fragments = []; page = 0
        while page is not None:
            output = subprocess.check_output([sys.executable, str(workspace / 'read-retained.py'), 'context', 'source_material', str(page)])
            record = json.loads(output); fragments.append(record['fragment']); call_id = 'read-' + str(page)
            history.extend([dict(type='function_call', call_id=call_id, name='exec', arguments=canonical({'command': 'python3 read-retained.py context source_material ' + str(page)}).decode()), dict(type='function_call_output', call_id=call_id, output='non-JSON diagnostic\n[]\n' + output.decode())]); page = record['next_page']
        self.assertEqual(''.join(fragments).encode(), canonical(context['source_material'])); self.assertEqual(len(builder.delivered_pages(context, history)['source_material']), 2)
        malformed = copy.deepcopy(history); malformed[0]['arguments'] = '{truncated'; self.assertNotIn(0, builder.delivered_pages(context, malformed).get('source_material', {}))
        malformed = copy.deepcopy(history); malformed[0]['arguments'] = canonical({'command': 'python3 read-retained.py context source_material invalid'}).decode(); self.assertNotIn(0, builder.delivered_pages(context, malformed).get('source_material', {}))

    def test_repair_handoff_rejects_changed_stage_and_nonimmutable_checkpoint_pointers(self):
        context = {'repair_directive': 'Synthetic repair', 'repair_packet': {'incident_sha256': 'a' * 64}}; handoff = builder.repair_handoff(context)
        for value in [dict(handoff, stage='assessment'), dict(handoff, baseline_checkpoint={'sha256': 'a' * 64}), dict(handoff, baseline_checkpoint='not a pointer')]:
            with self.subTest(value=value), self.assertRaises(ValueError): builder.repair_handoff(dict(context, native_repair_handoff=value))
        with self.assertRaises(ValueError): builder.repair_handoff({})

    def test_prospective_project_inspection_preserves_original_file_hashes_and_rejects_revision_mixing(self):
        root = self.root / 'project'; project(root); raw = generated_package.freeze(root); identity = uid(); item = dict(artifact_id=identity, sha256=digest(raw), version='fixture-v1')
        proposal = {'protocol': {'execution': {'runner': 'built-package-v1'}, 'configurations': [{'details': {'test_config': {'private_inputs': {'project.zip': item}}}}]}}
        with patch.object(store, 'artifact_bytes', return_value=raw) as read:
            result = builder.project_document(proposal, 'study'); self.assertEqual(result['archive'], item); self.assertEqual({r['path'] for r in result['files']}, {'experiment.py', 'requirements.lock', 'system-packages.lock.json', 'build-evidence.json'})
            for file in result['files']: self.assertEqual(digest(file['text'].encode()), file['sha256'])
            read.assert_called_once_with('study', identity, maximum=generated_package.MAX_ARCHIVE)
            proposal['protocol']['configurations'].append({'details': {'test_config': {'private_inputs': {'project.zip': dict(item, version='changed')}}}})
            with self.assertRaisesRegex(ValueError, 'exact project revision'): builder.project_document(proposal, 'study')
            proposal['protocol']['configurations'].pop(); read.return_value = raw + b'changed'
            with self.assertRaisesRegex(ValueError, 'original hash'): builder.project_document(proposal, 'study')
            self.assertIsNone(builder.project_document({'protocol': {}}, 'study'))

    def test_candidate_registration_supplies_real_artifacts_and_does_not_edit_the_original_manifest(self):
        project(self.root / 'project'); protocol = built_protocol()
        for configuration in protocol['configurations']:
            config = configuration['details']['test_config']; config.pop('package'); config.pop('private_inputs')
        candidate = {'protocol': protocol, 'experiment_type': 'new-measurement'}; raw = canonical(candidate); (self.root / 'candidate.json').write_bytes(raw); retained = []
        def retain(job, name, data, kind='builder-original'):
            retained.append((name, data, kind)); return dict(artifact_id=uid(), sha256=digest(data), version=builder.VERSION)
        with patch.object(builder, 'retain', side_effect=retain), patch.object(store, 'rpc', return_value={'recorded': True}) as register:
            result = builder.freeze_candidate({'id': uid(), 'lease_token': uid()}, self.root)
        self.assertEqual((self.root / 'candidate.json').read_bytes(), raw); self.assertEqual(result['protocol']['execution']['runner'], 'built-package-v1'); self.assertIn('private_inputs', result['protocol']['configurations'][0]['details']['test_config'])
        self.assertEqual(register.call_args.args[0], 'research_builder_package'); self.assertEqual([v[0] for v in retained], ['candidate-original.json', 'project.zip', 'package.py', 'candidate-compiled.json'])
        self.assertEqual(retained[2][1], Path(generated_package.__file__).read_bytes())

    def test_candidate_manifest_and_generated_tool_calls_have_strict_type_and_execution_bounds(self):
        self.assertIsNone(builder.freeze_candidate({}, self.root))
        for value in [[], {'protocol': {}}, {'protocol': {}, 'experiment_type': 'new-measurement', 'extra': True}]:
            (self.root / 'candidate.json').write_bytes(canonical(value))
            with self.assertRaisesRegex(ValueError, 'complete prospective'): builder.freeze_candidate({}, self.root)
        (self.root / 'candidate.json').write_bytes(b'x' * 64001)
        with self.assertRaisesRegex(ValueError, 'bounded regular'): builder.freeze_candidate({}, self.root)
        for name, arguments in [('unknown', {}), ('exec', {'timeoutSeconds': 46}), ('exec', {'timeoutSeconds': True})]:
            with self.subTest(name=name, arguments=arguments), self.assertRaises(ValueError): builder.validate_calls({'output': [{'type': 'function_call', 'name': name, 'arguments': canonical(arguments).decode()}]})
        builder.validate_calls({'output': [{'type': 'message'}, {'type': 'function_call', 'name': 'read', 'arguments': '{}'}, {'type': 'function_call', 'name': 'exec', 'arguments': '{"command":"python3 --version","host":"sandbox","timeoutSeconds":45}'}]})

    def test_catalog_is_source_scoped_and_labels_packages_as_unreviewed_candidates(self):
        row = dict(id=uid(), task_id=uid(), version='fixture-v1', candidate_artifact_id=uid(), project_artifact_id=uid())
        with patch.object(store, 'rows', return_value=[row]) as read: result = builder.catalog('study', 'source')
        read.assert_called_once_with('builder_packages', dict(study_id='eq.study', source_id='eq.source', order='created_at.desc', limit=2)); self.assertEqual(result[0]['read_request'], {'kind': 'package', 'id': row['id'], 'cursor': 0}); self.assertIn('successful execution not established', result[0]['status'])

    def test_bootstrap_moves_optional_feedback_out_whole_when_trusted_instructions_need_the_space(self):
        context = {'task': 'building', 'claim': {'description': 'Synthetic claim'}, 'builder_feedback': 'Exact implementation feedback. ' * 40}
        logical = {'system': [{'text': 'Fixed'}], 'builder': {'context': context}}
        message, initial = builder.bootstrap(logical, self.root, self.root, [])
        self.assertIn('builder_feedback', initial['inline_fields'])
        instruction = 'T' * (builder.BOOTSTRAP_BYTES - len(message.encode()) + len('Fixed') + 1)
        logical['system'][0]['text'] = instruction; text, manifest = builder.bootstrap(logical, self.root, self.root, [])
        self.assertIn(instruction, text); self.assertLessEqual(len(text.encode()), builder.BOOTSTRAP_BYTES); self.assertNotIn('builder_feedback', manifest['inline_fields']); self.assertEqual(json.loads((self.root / 'research-context.json').read_bytes()), context)

    def test_invalid_action_policies_and_oversized_progress_cannot_be_treated_as_trusted_context(self):
        policy = self.root / 'policy.json'
        with patch.object(builder, 'ACTION_POLICY_PATH', policy):
            for document in [{'version': 'unknown', 'mode': 'autonomous-lab-experiment'}, {'version': 'experiment-action-policy-v1', 'mode': 'unknown'}]:
                policy.write_bytes(canonical(document))
                with self.subTest(document=document), self.assertRaisesRegex(ValueError, 'policy version'): builder.action_policy()
        (self.root / 'large-source.py').write_bytes(b'x' * 1000001)
        with self.assertRaisesRegex(ValueError, 'Progress file exceeds'): builder.work_progress(self.root, {}, [])

    def test_context_delivery_refuses_all_fixed_path_symlinks_without_overwriting_their_targets(self):
        original = self.root / 'original'; original.write_bytes(b'Original independent artifact')
        logical = {'system': [{'text': 'Synthetic instruction'}], 'builder': {'context': {'task': 'building'}}}
        for filename in ['research-context.json', 'builder-continuation.json', 'work-packet.json']:
            workspace = self.root / filename.replace('.', '-'); workspace.mkdir(); (workspace / filename).symlink_to(original)
            with self.subTest(filename=filename), self.assertRaisesRegex(ValueError, 'symlink'): builder.bootstrap(logical, workspace, self.root, [])
            self.assertEqual(original.read_bytes(), b'Original independent artifact')

    def test_late_links_after_inventory_cannot_replace_the_fixed_continuation_or_work_packet(self):
        original=self.root/'original-late-artifact';original.write_bytes(b'Original independent bytes');progress=builder.work_progress
        for filename in ['builder-continuation.json','work-packet.json']:
            workspace=self.root/('late-'+filename);workspace.mkdir()
            def inventory(*args):
                value=progress(*args);(workspace/filename).symlink_to(original);return value
            with self.subTest(filename=filename),patch.object(builder,'work_progress',side_effect=inventory),self.assertRaisesRegex(ValueError,'cannot replace a symlink'):
                builder.bootstrap(dict(system=[],builder=dict(context={})),workspace,self.root,[])
            self.assertEqual(original.read_bytes(),b'Original independent bytes')

    def test_reaper_retains_live_or_unidentified_workspaces_and_does_not_repeat_completed_cleanup(self):
        with patch.object(builder.store,'rows') as rows:builder.reap(self.root/'missing-spool')
        rows.assert_not_called()
        for defect in ['missing-task','missing-workflow','live','retired']:
            spool=self.root/defect;identity=uid();directory=spool/'builder-workspaces'/identity;directory.mkdir(parents=True)
            if defect=='retired':(directory/'retired.json').write_bytes(b'Original retirement receipt')
            def rows(kind,query):
                if kind=='autonomy_tasks':return [] if defect=='missing-task' else [dict(id=identity,study_id=uid(),status='queued' if defect=='live' else 'blocked')]
                return [] if defect=='missing-workflow' else [dict(status='running' if defect=='live' else 'cancelled',deadline_at='2099-01-01T00:00:00Z')]
            with self.subTest(defect=defect),patch.object(builder.store,'rows',side_effect=rows),patch.object(builder,'container_action',return_value=[]) as container:builder.reap(spool)
            self.assertTrue(all(call.args[1]=='list' for call in container.call_args_list));self.assertFalse((directory/'retirement-pending.json').exists())

    def test_unbounded_required_work_packet_is_rejected_with_the_original_context_unchanged(self):
        context = {'deadline_at': 'x' * (builder.WORK_PACKET_BYTES + 1), 'claim': {'description': 'Original claim'}}
        original = canonical(context); inventory = {'implementation_files': []}
        with self.assertRaisesRegex(ValueError, 'Required work packet exceeds'): builder.work_packet(context, inventory, self.root)
        self.assertEqual(canonical(context), original)

    def test_artifact_registration_does_not_imply_byte_verification(self):
        raw = b'Original request'; job = dict(id=uid(), study_id=uid(), actor_id=uid())
        for stored, sha in [(b'changed after storage', digest(raw)), (raw, 'a' * 64)]:
            with patch.object(store, 'artifact', return_value={'sha256': sha}), patch.object(store, 'append') as append, patch.object(store, 'artifact_bytes', return_value=stored), self.subTest(stored=stored, sha=sha), self.assertRaisesRegex(ValueError, 'post-retrieval verification'): builder.retain(job, 'request.json', raw)
            append.assert_called_once()

    def test_checkpoint_transport_includes_zip_overhead_and_refuses_symlinked_sources(self):
        workspace = self.root / 'workspace'; workspace.mkdir(); original = self.root / 'original'; original.write_bytes(b'unchanged'); link = workspace / 'source'; link.symlink_to(original)
        with self.assertRaisesRegex(ValueError, 'checkpoint contains a symlink'): builder.checkpoint(workspace)
        link.unlink()
        # Exactly 4 MB of incompressible source fits the expanded-size budget,
        # but its actual deflate data and ZIP headers exceed the transport bound.
        for number in range(4): (workspace / str(number)).write_bytes(os.urandom(1000000))
        with self.assertRaisesRegex(ValueError, 'transport exceeds'): builder.checkpoint(workspace)
        self.assertEqual(original.read_bytes(), b'unchanged')

    def test_candidate_cannot_supply_a_different_runner_or_its_own_artifact_bindings(self):
        project(self.root / 'project'); baseline = built_protocol()
        for config in baseline['configurations']:
            config['details']['test_config'].pop('package'); config['details']['test_config'].pop('private_inputs')
        for kind in ['runner', 'artifact']:
            protocol = copy.deepcopy(baseline)
            if kind == 'runner': protocol['execution']['runner'] = 'unregistered-runner'
            else: protocol['configurations'][0]['details']['test_config']['package'] = {'artifact_id': uid()}
            raw = canonical(dict(protocol=protocol, experiment_type='new-measurement')); (self.root / 'candidate.json').write_bytes(raw)
            def retain(job, name, data, category='builder-original'): return dict(artifact_id=uid(), sha256=digest(data), version=builder.VERSION)
            with patch.object(builder, 'retain', side_effect=retain), patch.object(store, 'rpc') as register, self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'built-package-v1|server supplies real artifacts'): builder.freeze_candidate({}, self.root)
            register.assert_not_called(); self.assertEqual((self.root / 'candidate.json').read_bytes(), raw)

    def test_reaper_leaves_unknown_locked_and_active_task_resources_untouched(self):
        root = self.root / 'builder-workspaces'; root.mkdir(); (root / 'not-a-task').mkdir(); (root / 'regular-file').write_bytes(b'original'); (root / 'symlink').symlink_to(root, target_is_directory=True)
        identity = uid(); task = root / identity; task.mkdir()
        with patch.object(builder, 'container_action') as cleanup, patch.object(store, 'rows', return_value=[]): builder.reap(self.root); cleanup.assert_not_called()
        with patch.object(builder.fcntl, 'flock', side_effect=BlockingIOError('Other slice owns lock')), patch.object(builder, 'container_action') as cleanup, patch.object(store, 'rows') as read: builder.reap(self.root); cleanup.assert_not_called(); read.assert_not_called()
        study = uid()
        for workflows in [[], [{'status': 'running', 'deadline_at': '2099-01-01T00:00:00Z'}]]:
            with patch.object(store, 'rows', side_effect=[[{'id': identity, 'study_id': study, 'status': 'running'}], workflows]), patch.object(builder, 'container_action') as cleanup: builder.reap(self.root); cleanup.assert_not_called()
        self.assertEqual((root / 'regular-file').read_bytes(), b'original')

    def test_restore_rejects_unsafe_members_and_checkpoint_bounds_without_partial_outside_writes(self):
        def archive(members):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zipped:
                for name, data in members: zipped.writestr(name, data)
            return buffer.getvalue()
        destination = self.root / 'restore'; destination.mkdir()
        for name in ['../escape', '/absolute', 'a\\b', 'a//b', 'directory/']:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'Unsafe builder checkpoint'): builder.restore(archive([(name, b'original')]), destination)
        with self.assertRaisesRegex(ValueError, 'expanded bound'): builder.restore(archive([(str(i), b'x') for i in range(129)]), destination)
        with self.assertRaisesRegex(ValueError, 'expanded bound'): builder.restore(archive([(str(i), b'x' * 900000) for i in range(5)]), destination)
        with self.assertRaisesRegex(ValueError, 'transport bound'): builder.restore(b'x' * 4000001, destination)
        for count, size in [(1, 1000001), (129, 1), (5, 900000)]:
            workspace = self.root / (str(count) + '-' + str(size)); workspace.mkdir()
            for i in range(count): (workspace / str(i)).write_bytes(b'x' * size)
            with self.subTest(count=count, size=size), self.assertRaisesRegex(ValueError, 'checkpoint exceeds'): builder.checkpoint(workspace)

    def test_restore_cannot_follow_a_preexisting_workspace_symlink_to_an_outside_original(self):
        original = self.root / 'original'; original.write_bytes(b'Original must remain unchanged'); workspace = self.root / 'workspace'; workspace.mkdir(); (workspace / 'target').symlink_to(original)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive: archive.writestr('target', b'Untrusted replacement')
        with self.assertRaisesRegex(ValueError, 'symlink'): builder.restore(buffer.getvalue(), workspace)
        self.assertEqual(original.read_bytes(), b'Original must remain unchanged')

    def test_restore_replaces_hardlinks_atomically_and_removes_failed_temporary_files(self):
        original = self.root / 'original'; original.write_bytes(b'Original retained bytes'); workspace = self.root / 'workspace'; workspace.mkdir(); os.link(original, workspace / 'target')
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive: archive.writestr('target', b'Restored checkpoint bytes')
        builder.restore(buffer.getvalue(), workspace); self.assertEqual(original.read_bytes(), b'Original retained bytes'); self.assertEqual((workspace / 'target').read_bytes(), b'Restored checkpoint bytes')
        with patch.object(builder.os, 'replace', side_effect=OSError('Synthetic failed atomic replacement')), self.assertRaisesRegex(OSError, 'atomic replacement'): builder.restore(buffer.getvalue(), workspace)
        self.assertEqual(list(workspace.glob('.checkpoint-restore-*')), []); self.assertEqual(original.read_bytes(), b'Original retained bytes')
        link = self.root / 'linked-workspace'; link.symlink_to(workspace, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'workspace cannot be a symlink'): builder.restore(buffer.getvalue(), link)
        (workspace / 'directory-link').symlink_to(self.root, target_is_directory=True)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive: archive.writestr('directory-link/original', b'Must not replace original')
        with self.assertRaisesRegex(ValueError, 'workspace symlink'): builder.restore(buffer.getvalue(), workspace)
        self.assertEqual(original.read_bytes(), b'Original retained bytes')

    def test_restore_bad_zip_crc_and_partial_temporary_write_preserve_target_and_leave_no_partial_files(self):
        workspace = self.root / 'workspace'; workspace.mkdir(); target = workspace / 'target'; target.write_bytes(b'Original retained target'); content = b'Complete checkpoint content'; buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_STORED) as archive: archive.writestr('target', content)
        raw = buffer.getvalue(); offset = raw.index(content); corrupt = raw[:offset] + b'X' + raw[offset + 1:]
        with self.assertRaises(zipfile.BadZipFile): builder.restore(corrupt, workspace)
        self.assertEqual(target.read_bytes(), b'Original retained target'); self.assertEqual(list(workspace.glob('.checkpoint-restore-*')), [])
        original_temporary = builder.tempfile.NamedTemporaryFile
        class PartialWrite:
            def __init__(self, *args, **options): self.file = original_temporary(*args, **options); self.name = self.file.name
            def __enter__(self): return self
            def __exit__(self, *args): self.file.close()
            def write(self, data): self.file.write(data[:3]); raise OSError('Synthetic partial workspace write')
        with patch.object(builder.tempfile, 'NamedTemporaryFile', PartialWrite), self.assertRaisesRegex(OSError, 'partial workspace write'): builder.restore(raw, workspace)
        self.assertEqual(target.read_bytes(), b'Original retained target'); self.assertEqual(list(workspace.glob('.checkpoint-restore-*')), [])
        with patch.object(builder.tempfile, 'NamedTemporaryFile', side_effect=OSError('Synthetic file creation failure')), self.assertRaisesRegex(OSError, 'file creation failure'): builder.restore(raw, workspace)
        self.assertEqual(target.read_bytes(), b'Original retained target'); self.assertEqual(list(workspace.glob('.checkpoint-restore-*')), [])


if __name__ == '__main__': unittest.main()
