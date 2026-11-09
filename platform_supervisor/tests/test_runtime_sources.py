"""Installed broker evidence is distinct from repository references and candidates."""
import copy
import io
import json
import os
import tarfile
import tempfile
import sys
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'research_workflow'))
from platform_supervisor import runtime_sources as sources,self_runtime
from platform_supervisor.common import canonical,digest,Hold


def archive(files):
 out=io.BytesIO()
 with tarfile.open(fileobj=out,mode='w:gz') as stream:
  for name,raw in files.items():
   item=tarfile.TarInfo('repository/'+name);item.size=len(raw);stream.addfile(item,io.BytesIO(raw))
 return out.getvalue()


class RuntimeSources(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.installed=self.root/'active';self.workspace=self.root/'workspace';self.workspace.mkdir()
  self.files={'platform_supervisor/worker.py':b'raise RuntimeError("passive source only")',**{name:('installed '+name).encode() for name in sources.GATES}}
  for name,raw in self.files.items():
   path=self.installed/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
  (self.installed/'credentials.json').write_text('private-value-must-not-be-delivered')
  self.manifest=dict(version=self_runtime.VERSION,source_revision='a'*40,files={'worker.py':digest(self.files['platform_supervisor/worker.py'])},bundle_files={name:digest(raw) for name,raw in self.files.items()})
  self.owning={'assets/platform/'+name:('repository '+name).encode() for name in sources.GATES}
  self.owning.update({'bin/research-runtime-release.py':b'previous release','bin/supervisor-runtime-release.py':b'self release','.env.local':b'private-repository-value'})
  self.store=Mock();self.store.task=dict(id='11111111-1111-4111-8111-111111111111',repositories=[self_runtime.BACKEND]);self.store.spool=self.root/'spool'
  self.store.request.return_value=self.manifest;self.store.artifact.side_effect=lambda name,raw:dict(id=name,sha256=digest(raw))
  self.controller=SimpleNamespace(store=self.store,state={},github=Mock(),save_state=Mock())
  self.predecessor={'platform_supervisor/worker.py':self.files['platform_supervisor/worker.py'],
   'platform_supervisor/tests/test_self_runtime.py':b'passive predecessor tests',
   'platform_supervisor/tests/test_supervisor.py':b'other passive predecessor tests'}
  self.controller.github.main.return_value='b'*40
  self.controller.github.archive.side_effect=lambda repo,revision:archive(self.owning if repo==sources.INFRA else self.predecessor)

 def deliver(self):
  with patch.object(self_runtime,'ROOT',self.installed):return sources.deliver(self.controller,self.workspace)

 def test_actual_installed_bytes_and_repository_references_are_delivered_separately(self):
  before=copy.deepcopy(self.store.task);binding=self.deliver()
  index=json.loads((self.workspace/'references/runtime-source/index.json').read_bytes())
  self.assertEqual(binding,digest(canonical(index)))
  self.assertEqual(index['installed']['source_revision'],'a'*40);self.assertFalse(index['infrastructure']['installed_identity'])
  root=self.workspace/'references/runtime-source'
  for name,raw in self.files.items():self.assertEqual((root/index['installed']['directory']/name).read_bytes(),raw)
  self.assertEqual((root/index['infrastructure']['directory']/'assets/platform/research-runtime-gate.py').read_bytes(),self.owning['assets/platform/research-runtime-gate.py'])
  self.assertEqual(self.store.task,before);self.assertNotIn('sources',self.controller.state)
  retained=self.store.artifact.call_args.args[1]
  with zipfile.ZipFile(io.BytesIO(retained)) as bundle:
   contents=b''.join(bundle.read(n) for n in bundle.namelist())
   self.assertNotIn(b'private-value-must-not-be-delivered',contents)
   self.assertNotIn(b'private-repository-value',contents)
   self.assertTrue(all('.env' not in n and 'credentials.json' not in n for n in bundle.namelist()))
  self.store.artifact.reset_mock();self.deliver();self.store.artifact.assert_not_called()
  self.assertEqual(self.controller.github.archive.call_count,2)
  self.assertTrue(index['predecessor_tests']['actual_supervisor_source_matched'])
  self.assertFalse(index['predecessor_tests']['installed_files'])
  self.assertFalse(index['infrastructure']['installed_asset_matches']['research-runtime-gate.py'])

 def test_changed_missing_or_linked_installed_files_stop_before_repository_acquisition(self):
  path=self.installed/'research-runtime-gate.py';raw=path.read_bytes()
  for mode in ('changed','missing','linked'):
   with self.subTest(mode=mode):
    path.unlink()
    if mode=='changed':path.write_bytes(b'candidate changed gate')
    if mode=='linked':path.symlink_to(self.installed/'platform_supervisor/worker.py')
    with self.assertRaises(Hold):self.deliver()
    self.controller.github.main.assert_not_called();self.store.artifact.assert_not_called()
    if path.exists() or path.is_symlink():path.unlink()
    path.write_bytes(raw)

 def test_incomplete_activation_manifest_never_claims_installed_source(self):
  del self.manifest['bundle_files']['research-runtime-checks.py']
  with self.assertRaises(Hold):self.deliver()
  self.assertFalse((self.workspace/'references/runtime-source/index.json').exists())

 def test_modified_reference_and_linked_parent_are_rejected(self):
  self.deliver();root=self.workspace/'references/runtime-source'
  path=next((root/'installed').rglob('research-runtime-gate.py'));path.write_bytes(b'untrusted modification')
  with self.assertRaises(Hold):self.deliver()
  other=self.root/'linked-workspace';other.mkdir();(other/'references').symlink_to(self.root/'outside')
  with patch.object(self_runtime,'ROOT',self.installed),self.assertRaises(Hold):sources.deliver(self.controller,other)
  self.assertFalse((self.root/'outside').exists())

 def test_new_source_evidence_retains_blocker_and_unchanged_evidence_does_not_restart_it(self):
  raw=canonical(dict(version='platform-progress-v1',status='blocked',ready=False,remaining_blocker='Missing release source'))
  (self.workspace/'progress.json').write_bytes(raw)
  self.controller.state['repair_guard']=dict(waiting=True,unchanged_slices=2)
  self.deliver()
  self.assertFalse((self.workspace/'progress.json').exists())
  self.assertEqual((self.workspace/'previous-candidates'/('blocked-runtime-source-'+digest(raw)+'.json')).read_bytes(),raw)
  self.assertFalse(self.controller.state['repair_guard']['waiting'])
  self.store.artifact.assert_any_call('blocked-before-runtime-source-delivery.json',raw)
  (self.workspace/'progress.json').write_bytes(raw);self.deliver()
  self.assertEqual((self.workspace/'progress.json').read_bytes(),raw)

 def test_unrelated_frontend_tasks_do_not_acquire_runtime_sources(self):
  self.store.task['repositories']=['jumpserve-networks/jumpserve-front-end']
  self.assertIsNone(self.deliver());self.store.request.assert_not_called();self.controller.github.main.assert_not_called()

 def test_unselected_reference_files_are_omitted_and_required_release_source_is_mandatory(self):
  self.owning['assets/platform/notes.txt']=b'Unselected file';self.predecessor['platform_supervisor/tests/notes.txt']=b'Unselected test notes'
  self.deliver();retained=self.store.artifact.call_args.args[1]
  with zipfile.ZipFile(io.BytesIO(retained)) as bundle:self.assertFalse(any(name.endswith('notes.txt') for name in bundle.namelist()))
  del self.owning['bin/research-runtime-release.py'];other=self.root/'second';other.mkdir()
  self.controller.store.spool=self.root/'other-spool'
  with patch.object(self_runtime,'ROOT',self.installed),self.assertRaisesRegex(Hold,'release source is incomplete'):sources.deliver(self.controller,other)

 def test_linked_index_and_oversized_compressed_evidence_cannot_be_published(self):
  self.deliver();index=self.workspace/'references/runtime-source/index.json';index.unlink();private=self.root/'private.json';private.write_bytes(b'Original private data');index.symlink_to(private)
  with self.assertRaisesRegex(Hold,'index cannot be a link'):self.deliver()
  self.assertEqual(private.read_bytes(),b'Original private data');index.unlink()
  reference,owning=sources.infrastructure(self.controller);owning['assets/platform/large.py']=os.urandom(5_000_001)
  reference['files']['assets/platform/large.py']=digest(owning['assets/platform/large.py']);self.controller.state.clear();self.store.artifact.reset_mock()
  with patch.object(sources,'infrastructure',return_value=(reference,owning)),self.assertRaisesRegex(Hold,'artifact exceeds its bound'):self.deliver()
  self.store.artifact.assert_not_called()


if __name__=='__main__':unittest.main()
