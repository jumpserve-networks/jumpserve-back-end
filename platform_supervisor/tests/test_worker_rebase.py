"""Retained generic candidates against concurrent main updates, without GitHub."""
import base64
import copy
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import uuid
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from platform_supervisor import source, worker
from platform_supervisor.common import canonical, digest, Hold, POLICY

FRONT = 'jumpserve-networks/jumpserve-front-end'; BACK = 'jumpserve-networks/jumpserve-back-end'


class WorkerRebase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='supervisor-rebase-unit-'); self.addCleanup(temporary.cleanup); self.root = Path(temporary.name); self.workspace = self.root / 'workspace'; self.workspace.mkdir()
        self.store = MagicMock(); self.store.task = dict(id=str(uuid.uuid4()), repositories=[FRONT, BACK]); self.rpc_calls = []; self.operations = {}; self.artifacts = {}
        self.store.rpc.side_effect = self.rpc; self.store.artifact.side_effect = self.artifact; self.store.rows.side_effect = self.rows; self.store.request.side_effect = self.request
        self.github = MagicMock(); self.github.main.side_effect = lambda repository: 'b' * 40 if repository == FRONT else 'a' * 40; self.github.get.return_value = dict(state='open', merged=False)
        self.controller = worker.Controller(self.store, self.github, 'sha256:' + 'e' * 64); self.controller.state = dict(sources={}, changes=[], generation=3, research_runtime={'qualified': True}, supervisor_runtime={'scope': {}}, runtime_reviewed_source='a' * 40)
        for repository in [FRONT, BACK]:
            folder = self.workspace / POLICY['repositories'][repository]['directory']; folder.mkdir(); (folder / 'source.py').write_bytes(b'Original base\n'); self.controller.state['sources'][repository] = dict(revision='a' * 40, snapshot=source.snapshot(folder))
        self.front = self.workspace / POLICY['repositories'][FRONT]['directory']; self.back = self.workspace / POLICY['repositories'][BACK]['directory']
        self.fresh = self.root / 'fresh'; self.fresh.mkdir(); (self.fresh / 'source.py').write_bytes(b'Original base\n'); (self.fresh / 'concurrent.py').write_bytes(b'Concurrent preserved work\n')
        self.github.archive.side_effect = lambda repository, revision: self.archive(self.fresh)
    def rpc(self, operation, payload=None):
        self.rpc_calls.append((operation, copy.deepcopy(payload)))
        if operation == 'lease': return dict(paused=False, cancel_requested=False, deadline_at='2099-01-01T00:00:00Z')
        if operation == 'begin-operation':
            value = dict(payload, status='pending', replayed=False); self.operations[payload['id']] = value; return value
        if operation == 'finish-operation': self.operations[payload['id']].update(payload); return payload
        if operation in ['change', 'progress', 'release']: return payload
        raise AssertionError(operation)
    def artifact(self, name, raw):
        identity = str(uuid.uuid5(uuid.UUID(self.store.task['id']), name + digest(raw))); record = dict(id=identity, sha256=digest(raw), storage_path=self.store.task['id'] + '/' + digest(raw)); self.artifacts[identity] = (record, raw); return record
    def rows(self, table, **filters):
        if table == 'artifacts': return [record for record, raw in self.artifacts.values() if 'eq.' + record['id'] == filters['id']]
        if table == 'operations': return list(self.operations.values())
        raise AssertionError(table)
    def request(self, path, **options): return next(raw for record, raw in self.artifacts.values() if path.endswith(record['storage_path']))
    def archive(self, folder):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
            for file in folder.rglob('*'):
                if file.is_file(): archive.add(file, arcname='source/' + file.relative_to(folder).as_posix())
        return buffer.getvalue()
    def change(self, repository=FRONT, files=None, pr_number=17):
        files = files or [dict(path='source.py', mode='100644', content=base64.b64encode(b'Proposed tested correction\n').decode())]
        record = self.artifact('patch-' + repository, canonical(dict(version='platform-patch-v1', files=files)))
        return dict(id=str(uuid.uuid4()), repository=repository, base_sha='a' * 40, component='frontend' if repository == FRONT else 'backend', patch_artifact_id=record['id'], pr_number=pr_number, status='published')

    def test_rebase_preserves_nonoverlapping_edits_concurrent_work_and_exact_original_patch(self):
        change = self.change(); original_artifact = copy.deepcopy(self.artifacts); original_task = copy.deepcopy(self.store.task)
        with self.assertRaisesRegex(Hold, 'Fresh main revisions') as error: self.controller.rebase(self.workspace, [change])
        self.assertEqual(error.exception.status, 'implementing'); self.assertEqual((self.front / 'source.py').read_bytes(), b'Proposed tested correction\n'); self.assertEqual((self.front / 'concurrent.py').read_bytes(), b'Concurrent preserved work\n'); self.assertEqual(json.loads((self.workspace / 'rebase-conflicts.json').read_bytes()), [])
        self.assertEqual(self.store.task, original_task)
        for identity, original in original_artifact.items(): self.assertEqual(self.artifacts[identity], original)
        self.assertEqual(change['status'], 'superseded'); self.assertFalse(self.controller.state['tested']); self.assertEqual(self.controller.state['changes'], []); self.assertEqual(self.controller.state['generation'], 4); self.assertNotIn('research_runtime', self.controller.state); self.assertNotIn('supervisor_runtime', self.controller.state); self.assertEqual(self.controller.state['runtime_reviewed_source'], 'a' * 40)
        self.github.request.assert_called_once_with('PATCH', '/repos/' + FRONT + '/pulls/17', {'state': 'closed'}); self.assertTrue(any(op == 'progress' for op, _ in self.rpc_calls)); self.assertTrue(all(op['status'] == 'confirmed' for op in self.operations.values()))

    def test_conflicting_edit_and_deletion_are_retained_without_overwriting_new_main(self):
        (self.fresh / 'source.py').write_bytes(b'Concurrent source correction\n'); (self.fresh / 'deleted.py').write_bytes(b'Concurrent retained file\n')
        files = [dict(path='source.py', mode='100644', content=base64.b64encode(b'Old proposed correction\n').decode()), dict(path='deleted.py', mode='100644', content=None)]; change = self.change(files=files, pr_number=None)
        with self.assertRaises(Hold): self.controller.rebase(self.workspace, [change])
        self.assertEqual((self.front / 'source.py').read_bytes(), b'Concurrent source correction\n'); self.assertEqual((self.front / 'deleted.py').read_bytes(), b'Concurrent retained file\n')
        conflicts = json.loads((self.workspace / 'rebase-conflicts.json').read_bytes()); self.assertEqual(len(conflicts), 2); self.assertEqual((self.workspace / conflicts[0]['previous_candidate']).read_bytes(), b'Old proposed correction\n'); self.assertTrue(conflicts[1]['previous_proposed_deletion']); self.assertIsNone(conflicts[1]['previous_candidate']); self.github.request.assert_not_called()

    def test_rebase_skips_derived_runtime_approval_and_closes_other_unadvanced_candidate_without_editing_it(self):
        approval = dict(path=worker.research_runtime.APPROVAL, mode='100644', content=base64.b64encode(b'Old derived approval').decode()); front = self.change(files=[approval]); back = self.change(BACK); self.github.get.return_value = dict(state='closed', merged=False)
        with self.assertRaises(Hold): self.controller.rebase(self.workspace, [front, back])
        self.assertFalse((self.front / worker.research_runtime.APPROVAL).exists()); self.assertEqual((self.back / 'source.py').read_bytes(), b'Original base\n'); self.assertEqual(front['status'], 'superseded'); self.assertEqual(back['status'], 'superseded'); self.github.request.assert_not_called(); self.assertEqual(sum(c.args[1].startswith('pulls/') for c in self.github.get.call_args_list), 2)

    def test_merged_or_unadvanced_candidate_is_not_rebased_and_external_merge_blocks_supersession(self):
        change = self.change(); change['merge_sha'] = 'c' * 40; self.controller.rebase(self.workspace, [change]); self.github.main.assert_not_called(); del change['merge_sha']; self.github.main.side_effect = None; self.github.main.return_value = 'a' * 40; self.controller.rebase(self.workspace, [change]); self.github.archive.assert_not_called()
        self.github.main.return_value = 'b' * 40; self.github.get.return_value = dict(state='closed', merged=True)
        with self.assertRaisesRegex(Hold, 'externally merged') as error: self.controller.rebase(self.workspace, [change])
        self.assertEqual(error.exception.status, 'reconciling'); self.assertEqual(change['status'], 'published'); self.assertFalse(self.controller.state.get('tested', False)); self.assertEqual(next(iter(self.operations.values()))['status'], 'unknown')

    def test_patch_reader_checks_the_exact_retained_bytes_before_using_a_candidate(self):
        change = self.change(); raw, record = self.controller.patch_bytes(change); self.assertEqual(digest(raw), record['sha256'])
        with patch.object(self.store, 'request', return_value=raw + b'Changed'), self.assertRaisesRegex(Hold, 'Frozen patch hash differs'): self.controller.patch_bytes(change)

    def test_restore_replays_exact_work_files_and_modes_but_refuses_unsafe_metadata_and_deletions(self):
        files = [dict(path='new.sh', mode='100755', content=base64.b64encode(b'#!/bin/sh\nexit 0\n').decode()), dict(path='source.py', mode='100644', content=None)]
        data = source.checkpoint({FRONT: {'files': files}, BACK: {'files': []}}, {'proposal.json': base64.b64encode(b'{"ready":false}').decode()}); record = self.artifact('working.zip', data); self.controller.state['working_artifact'] = {'path': record['storage_path'], 'sha256': record['sha256']}
        self.controller.restore_work(self.workspace); self.assertFalse((self.front / 'source.py').exists()); self.assertEqual((self.front / 'new.sh').stat().st_mode & 0o777, 0o755); self.assertEqual((self.workspace / 'proposal.json').read_bytes(), b'{"ready":false}')
        for metadata in [[], {str(i): '' for i in range(201)}, {'unknown.json': ''}, {'.env.private': ''}]:
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w') as archive:
                for repository in [FRONT, BACK]: archive.writestr(POLICY['repositories'][repository]['directory'] + '.json', canonical({'files': []}))
                archive.writestr('work-packet.json', canonical(metadata))
            record = self.artifact('invalid-work.zip', buffer.getvalue()); self.controller.state['working_artifact'] = {'path': record['storage_path'], 'sha256': record['sha256']}
            with self.subTest(metadata=metadata), self.assertRaisesRegex(Hold, 'Invalid work packet|Unsafe work packet'): self.controller.restore_work(self.workspace)


if __name__ == '__main__': unittest.main()
