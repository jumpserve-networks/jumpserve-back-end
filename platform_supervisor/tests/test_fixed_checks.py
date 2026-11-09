"""Fixed check commands and retained receipts with no Docker or network calls."""
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from platform_supervisor import checks
from platform_supervisor.common import Hold, canonical, digest
from platform_supervisor.native import CleanupUnknown

FRONT = 'jumpserve-networks/jumpserve-front-end'
BACK = 'jumpserve-networks/jumpserve-back-end'
INFRA = 'jumpserve-networks/jumpserve-infra'


class FixedChecks(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='fixed-checks-unit-'); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); self.directory = self.root / 'candidate'; self.directory.mkdir()
        self.store = MagicMock(); self.store.spool = self.root / 'spool'; self.store.task = {'id': '6ef153bc-f708-44da-9261-41ba8a10abb3'}
        self.flags = dict(cancel_requested=False, paused=False, deadline_at='2099-01-01T00:00:00Z'); self.store.rpc.side_effect = lambda action: copy.deepcopy(self.flags)
        self.artifacts = {}; self.store.artifact.side_effect = self.artifact
        self.github = MagicMock(); self.github.archive.return_value = self.source_archive()
        self.output = subprocess.CompletedProcess(['synthetic fixed command'], 0, stdout=b'Original zero=0, missing=null\n', stderr=b'')
        self.run = self.enterContext(patch.object(checks.subprocess, 'run', return_value=self.output))
        self.image = 'sha256:' + 'f' * 64
    def artifact(self, name, raw):
        self.artifacts[name] = raw; return dict(id=digest(raw), sha256=digest(raw), byte_count=len(raw))
    def source_archive(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
            raw = b'# Synthetic public source, never executed\n'; info = tarfile.TarInfo('repository-root/runtime.py'); info.size = len(raw); info.mode = 0o644; archive.addfile(info, io.BytesIO(raw))
        return buffer.getvalue()
    def pins(self):
        for name, value in [('research-workflow-runtime.json', {'source_revision': 'a' * 40}), ('real-world-runtime.json', {'revision': 'b' * 40}), ('openclaw-worker-runtime.json', {'version': 1}), ('autonomy-pdf-runtime.json', {'version': 1})]: (self.directory / name).write_bytes(canonical(value))
    def policy(self, commands):
        policy = copy.deepcopy(checks.POLICY)
        for repository in [FRONT, BACK, INFRA]: policy['repositories'][repository]['checks'] = commands
        return patch.object(checks, 'POLICY', policy)

    def test_runtime_prerequisites_use_exact_public_revisions_and_a_hash_bound_complete_cache(self):
        self.pins(); checks.prepare_runtime_inputs(self.store, self.directory, self.image, self.github)
        self.assertEqual(self.github.archive.call_args_list[0].args, (BACK, 'a' * 40)); self.assertEqual(self.github.archive.call_args_list[1].args, (BACK, 'b' * 40))
        self.assertEqual((self.directory / '.runtime-research/runtime.py').read_bytes(), b'# Synthetic public source, never executed\n')
        commands = [call.args[0] for call in self.run.call_args_list]; self.assertEqual(len(commands), 3)
        for command in commands:
            self.assertEqual(command[:3], ['docker', 'run', '--rm']); self.assertIn('--cap-drop', command); self.assertIn('no-new-privileges', command); self.assertEqual(command[command.index('--network') + 1], 'bridge')
            self.assertNotIn('/var/run/docker.sock', ' '.join(command)); self.assertNotIn('AWS_ACCESS_KEY_ID', ' '.join(command)); self.assertIn(self.image, command)
        self.assertEqual(commands[0][-5:], ['python3', '-B', 'bin/prepare-research-runtime.py', '--backend', '.runtime-research'])
        self.assertEqual(commands[1][-5:], ['python3', '-B', 'bin/prepare-openclaw-worker.py', '--backend', '.runtime-research']); self.assertEqual(commands[2][-3:], ['python3', '-B', 'bin/prepare-autonomy-runtime.py'])
        for name in ['.runtime-openclaw', '.runtime-autonomy-python']: (self.directory / name).mkdir()
        marker = self.store.spool / self.store.task['id'] / 'dependencies/runtime-inputs'; self.assertEqual(marker.stat().st_mode & 0o777, 0o600)
        self.run.reset_mock(); self.github.reset_mock(); checks.prepare_runtime_inputs(self.store, self.directory, self.image, self.github)
        self.run.assert_not_called(); self.github.archive.assert_not_called()
        (self.directory / 'autonomy-pdf-runtime.json').write_bytes(canonical({'version': 2})); checks.prepare_runtime_inputs(self.store, self.directory, self.image, self.github)
        self.assertEqual(self.run.call_count, 3)

    def test_runtime_preparation_stops_at_owner_deadline_invalid_pin_failure_and_unknown_cleanup_boundaries(self):
        self.pins(); marker = self.store.spool / self.store.task['id'] / 'dependencies/runtime-inputs'
        (self.directory / 'research-workflow-runtime.json').write_bytes(canonical({'source_revision': 'main'}))
        with self.assertRaisesRegex(Hold, 'malformed'): checks.prepare_runtime_inputs(self.store, self.directory, self.image, self.github)
        self.run.assert_not_called(); self.pins()
        for changed, status in [(dict(cancel_requested=True), 'implementing'), (dict(paused=True), 'implementing'), (dict(deadline_at='2000-01-01T00:00:00Z'), 'deadline')]:
            original = copy.deepcopy(self.flags); self.flags.update(changed)
            with self.subTest(changed=changed), self.assertRaises(Hold) as caught: checks.prepare_runtime_inputs(self.store, self.directory, self.image, self.github)
            self.assertEqual(caught.exception.status, status); self.flags = original
        self.output.returncode = 2
        with self.assertRaisesRegex(Hold, 'hash check'): checks.prepare_runtime_inputs(self.store, self.directory, self.image, self.github)
        self.assertFalse(marker.exists()); self.assertEqual(json.loads(self.artifacts['runtime-preparation-0.json'])['exit_code'], 2)
        self.output.returncode = 0
        for cleanup, expected in [(self.output, Hold), (RuntimeError('Synthetic cleanup acknowledgment lost'), CleanupUnknown)]:
            self.run.side_effect = [subprocess.TimeoutExpired(['synthetic command'], 300), cleanup]
            with self.subTest(expected=expected), self.assertRaises(expected) as caught: checks.prepare_runtime_inputs(self.store, self.directory, self.image, self.github)
            self.assertEqual(caught.exception.status, 'reconciling' if expected is CleanupUnknown else 'investigating')
            self.assertEqual(self.run.call_args.args[0][:3], ['docker', 'rm', '-f']); self.assertTrue(self.run.call_args.kwargs['check'])
        self.assertFalse(marker.exists())

    def test_dependency_cache_is_bound_to_the_retained_lockfile_and_runs_without_package_scripts_or_credentials(self):
        self.assertIsNone(checks.prepare_dependencies(self.store, FRONT, self.directory, self.image)); self.run.assert_not_called()
        lock = self.directory / 'package-lock.json'; lock.write_bytes(b'{"lockfileVersion":3,"original_zero":0}')
        self.assertIsNone(checks.prepare_dependencies(self.store, FRONT, self.directory, self.image))
        command = self.run.call_args.args[0]; self.assertEqual(command[-3:], ['npm', 'ci', '--ignore-scripts']); self.assertEqual(command[command.index('--network') + 1], 'bridge')
        receipt = json.loads(self.artifacts['dependency-preparation-jumpserve-front-end.json']); self.assertTrue(receipt['credential_free']); self.assertEqual(receipt['package_lock_sha256'], digest(lock.read_bytes()))
        (self.directory / 'node_modules').mkdir(); self.run.reset_mock(); checks.prepare_dependencies(self.store, FRONT, self.directory, self.image); self.run.assert_not_called()
        lock.write_bytes(b'{"lockfileVersion":3,"changed":true}'); checks.prepare_dependencies(self.store, FRONT, self.directory, self.image); self.run.assert_called_once()
        marker = self.store.spool / self.store.task['id'] / 'dependencies/jumpserve-front-end'; self.assertEqual(marker.read_text(), digest(lock.read_bytes()))
        marker.unlink(); self.output.returncode = 1; self.output.stdout = b'x' * 12001 + b'\xff'
        receipt = checks.prepare_dependencies(self.store, FRONT, self.directory, self.image); self.assertEqual(receipt['exit_code'], 1); self.assertTrue(receipt['stdout_tail'].endswith('\ufffd')); self.assertEqual(len(receipt['stdout_tail']), 12000); self.assertFalse(marker.exists())

    def test_dependency_preparation_requires_runtime_acquisition_and_honors_controls_before_starting(self):
        with self.assertRaisesRegex(Hold, 'Trusted pinned runtime'): checks.prepare_dependencies(self.store, INFRA, self.directory, self.image)
        with patch.object(checks, 'prepare_runtime_inputs') as runtime:
            checks.prepare_dependencies(self.store, INFRA, self.directory, self.image, self.github)
        runtime.assert_called_once_with(self.store, self.directory, self.image, self.github)
        (self.directory / 'package-lock.json').write_bytes(b'{}')
        for changed, status in [(dict(cancel_requested=True), 'implementing'), (dict(paused=True), 'implementing'), (dict(deadline_at='2000-01-01T00:00:00Z'), 'deadline')]:
            original = copy.deepcopy(self.flags); self.flags.update(changed)
            with self.subTest(changed=changed), self.assertRaises(Hold) as caught: checks.prepare_dependencies(self.store, FRONT, self.directory, self.image)
            self.assertEqual(caught.exception.status, status); self.flags = original
        self.run.assert_not_called()
        for cleanup, expected in [(self.output, Hold), (RuntimeError('Synthetic cleanup failure'), CleanupUnknown)]:
            self.run.side_effect = [subprocess.TimeoutExpired(['synthetic command'], 600), cleanup]
            with self.subTest(expected=expected), self.assertRaises(expected) as caught: checks.prepare_dependencies(self.store, FRONT, self.directory, self.image)
            self.assertEqual(caught.exception.status, 'reconciling' if expected is CleanupUnknown else 'investigating')

    def test_fixed_checks_preserve_source_identity_disable_network_and_mount_only_the_trusted_backend_gate(self):
        (self.directory / 'source.py').write_bytes(b'# Original reviewed source\n')
        gate = self.root / 'installed-runtime-gate.py'; gate.write_bytes(b'# Installed trusted gate, never executed\n')
        with self.policy([['npm', 'ci'], ['npm', 'test']]), patch('platform_supervisor.self_runtime.gate', return_value=types.SimpleNamespace(__file__=str(gate))):
            receipt, artifact = checks.execute(self.store, BACK, self.directory, self.image)
        self.assertTrue(receipt['passed']); self.assertTrue(receipt['source_unchanged']); self.assertEqual(artifact['sha256'], digest(canonical(receipt)))
        commands = [call.args[0] for call in self.run.call_args_list]
        self.assertEqual([command[command.index('--network') + 1] for command in commands], ['bridge', 'none'])
        for command in commands:
            self.assertIn('type=bind,source=' + str(gate.resolve()) + ',target=/supervisor-runtime-gate.py,readonly', command)
            self.assertEqual(command[command.index('--memory') + 1], '3g'); self.assertEqual(command[command.index('--memory-swap') + 1], '3g'); self.assertIn('NODE_OPTIONS=--max-old-space-size=1536', command)
            self.assertLess(command.index('--mount'), command.index(self.image)); self.assertNotIn('--privileged', command)
        self.assertEqual(receipt['results'][0]['stdout_tail'], 'Original zero=0, missing=null\n'); self.assertEqual(receipt['reviewer'], {'type': 'automated', 'independent': False})
        self.assertEqual(json.loads(self.artifacts['checks-jumpserve-back-end.json']), receipt)

    def test_failed_timeout_or_source_mutating_check_never_produces_a_passing_receipt(self):
        (self.directory / 'source.py').write_bytes(b'# Original reviewed source\n')
        self.output.returncode = 1
        with self.policy([['npm', 'test'], ['npm', 'run', 'build']]): receipt, _ = checks.execute(self.store, FRONT, self.directory, self.image)
        self.assertFalse(receipt['passed']); self.assertEqual(len(receipt['results']), 1); self.assertEqual(self.run.call_count, 1)
        self.output.returncode = 0; self.output.stdout = b'x' * 4_000_001
        def mutate(*args, **options):
            (self.directory / 'source.py').write_bytes(b'# Candidate mutated by a check\n'); return self.output
        self.run.side_effect = mutate
        with self.policy([['npm', 'test']]): receipt, _ = checks.execute(self.store, FRONT, self.directory, self.image)
        self.assertFalse(receipt['passed']); self.assertFalse(receipt['source_unchanged']); self.assertEqual(len(self.artifacts['check-jumpserve-front-end-0-stdout.txt']), 4_000_000)
        self.run.side_effect = [subprocess.TimeoutExpired(['synthetic command'], 900), self.output]
        with self.policy([['npm', 'test']]): receipt, _ = checks.execute(self.store, FRONT, self.directory, self.image)
        self.assertFalse(receipt['passed']); self.assertIsNone(receipt['results'][0]['exit_code']); self.assertIn('900 seconds', receipt['results'][0]['reason'])
        self.run.side_effect = [subprocess.TimeoutExpired(['synthetic command'], 900), RuntimeError('Unknown cleanup')]
        with self.policy([['npm', 'test']]), self.assertRaises(CleanupUnknown) as caught: checks.execute(self.store, FRONT, self.directory, self.image)
        self.assertEqual(caught.exception.status, 'reconciling')

    def test_owner_pause_cancel_and_deadline_are_rechecked_between_fixed_commands(self):
        for changed, status in [(dict(cancel_requested=True), 'cancelled'), (dict(paused=True), 'testing'), (dict(deadline_at='2000-01-01T00:00:00Z'), 'deadline')]:
            self.run.reset_mock(); original = copy.deepcopy(self.flags); self.flags.update(changed)
            with self.subTest(changed=changed), self.policy([['npm', 'test']]), self.assertRaises(Hold) as caught: checks.execute(self.store, FRONT, self.directory, self.image)
            self.assertEqual(caught.exception.status, status); self.run.assert_not_called(); self.flags = original
        self.store.rpc.side_effect = [self.flags, dict(self.flags, cancel_requested=True)]
        with self.policy([['npm', 'test'], ['npm', 'run', 'build']]), self.assertRaisesRegex(Hold, 'Cancellation honored'): checks.execute(self.store, FRONT, self.directory, self.image)
        self.run.assert_called_once()


if __name__ == '__main__': unittest.main()
