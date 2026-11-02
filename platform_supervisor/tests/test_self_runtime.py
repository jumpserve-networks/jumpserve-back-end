"""Frozen self-repair scope, retained qualification and exact release routing."""
import base64,copy,json,shutil,tempfile,unittest,subprocess
from pathlib import Path
from unittest.mock import Mock,patch
from platform_supervisor import self_runtime,source,checks
from platform_supervisor.common import canonical,digest,Hold
from platform_supervisor.releases import Releases

class SelfRuntime(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.previous=self.root/'previous/platform_supervisor';self.candidate=self.root/'candidate/platform_supervisor'
  shutil.copytree(Path(self_runtime.__file__).parent,self.previous,ignore=shutil.ignore_patterns('__pycache__'));shutil.copytree(self.previous,self.candidate)
  self.gate=self_runtime.gate();self.manifest=dict(version=self_runtime.VERSION,source_revision='a'*40,files=self.gate.files(self.previous))
 def changed(self):
  path=self.candidate/'documentation.py';path.write_text(path.read_text()+'\n\ndef retained_topic_name(topic):\n    return topic.strip()\n')
  (self.candidate/'tests/test_new_topic.py').write_text('import unittest\nclass Topic(unittest.TestCase):\n def test_topic(self):\n  from platform_supervisor.documentation import retained_topic_name\n  self.assertEqual(retained_topic_name(" topic "),"topic")\n')
 def test_operational_helper_and_new_regression_pass_while_generic_publication_stays_fenced(self):
  baseline=source.snapshot(self.candidate.parent);self.changed();scope=self.gate.inspect(self.previous,self.candidate,self.manifest)
  with self.assertRaises(Hold):source.freeze(baseline,self.candidate.parent)
  patch=source.freeze(baseline,self.candidate.parent,supervisor_scope=scope)
  self.assertEqual({v['path'] for v in patch['files']},{'platform_supervisor/documentation.py','platform_supervisor/tests/test_new_topic.py'})
  with self.assertRaises(Hold):source.apply_patch(patch,self.previous.parent)
  source.apply_patch(patch,self.previous.parent,supervisor_scope=scope)
  self.assertEqual((self.previous/'documentation.py').read_bytes(),(self.candidate/'documentation.py').read_bytes())
 def test_frozen_credentials_policy_source_and_transport_cannot_be_approved_by_candidate_metadata(self):
  for name in ('credentials.py','policy.json','source.py','store.py'):
   with self.subTest(name=name):
    path=self.candidate/name;old=path.read_bytes();path.write_bytes(old+b'\n# candidate change\n')
    with self.assertRaises(ValueError):self.gate.inspect(self.previous,self.candidate,self.manifest)
    scope=dict(version=self_runtime.VERSION,candidate_paths={'platform_supervisor/'+name:digest(path.read_bytes())})
    with self.assertRaises(Hold):source.freeze(source.snapshot(self.previous.parent),self.candidate.parent,supervisor_scope=scope)
    path.write_bytes(old)
  self.assertEqual(self.gate.KERNEL,source.FROZEN_SUPERVISOR_FILES)
 def test_original_tests_and_critical_admission_methods_remain_exact(self):
  for name,before,after in [('worker.py','Cancellation honored at a safe boundary','Ignore cancellation'),('native.py','Provider outcome ','Invented outcome '),('checks.py','Cancellation honored between fixed checks','Ignore cancellation')]:
   path=self.candidate/name;original=path.read_text();self.assertIn(before,original);path.write_text(original.replace(before,after))
   with self.assertRaises(ValueError):self.gate.inspect(self.previous,self.candidate,self.manifest)
   path.write_text(original)
  path=next((self.candidate/'tests').glob('test_*.py'));path.write_bytes(path.read_bytes()+b'\n# changed original test\n');self.changed()
  with self.assertRaises(ValueError):self.gate.inspect(self.previous,self.candidate,self.manifest)
 def test_predecessor_hash_missing_source_and_import_time_effects_are_refused(self):
  self.changed();bad=copy.deepcopy(self.manifest);bad['files']['worker.py']='0'*64
  with self.assertRaises(ValueError):self.gate.inspect(self.previous,self.candidate,bad)
  (self.candidate/'new_tool.py').write_text('import os\nos.system("never execute this")\n')
  with self.assertRaises(ValueError):self.gate.inspect(self.previous,self.candidate,self.manifest)
  (self.candidate/'new_tool.py').unlink();(self.candidate/'repair.py').unlink()
  with self.assertRaises(ValueError):self.gate.inspect(self.previous,self.candidate,self.manifest)
 def test_fixed_release_route_requires_derived_qualification_and_binds_original_revision(self):
  controller=Mock();controller.state={}
  with self.assertRaises(Hold):self_runtime.release_request(controller,dict(merge_sha='b'*40))
  controller.state=dict(supervisor_runtime=dict(certificate=dict(passed=True),certificate_artifact='certificate',previous_manifest=self.manifest));controller.github.main.return_value='c'*40
  request=self_runtime.release_request(controller,dict(merge_sha='b'*40));self.assertEqual(request['previous_revision'],'a'*40)
  self.assertEqual(Releases(controller.github).target(request),(self_runtime.INFRA,'supervisor-runtime-release.yml'))
 def test_independent_backend_checker_mounts_the_actual_frozen_gate_outside_candidate_source(self):
  installed=Path(self.gate.__file__).resolve();workspace=self.root/'workspace';workspace.mkdir()
  frozen=self.root/'supervisor-runtime-gate.py';frozen.write_bytes(installed.read_bytes())
  with patch.object(self_runtime,'ROOT',workspace):
   self.assertEqual(Path(self_runtime.gate().__file__),frozen)
  store=Mock();store.task={'id':'synthetic'};store.rpc.return_value=dict(cancel_requested=False,paused=False,deadline_at='2099-01-01T00:00:00Z');store.artifact.return_value={'id':'artifact'}
  with patch.object(checks,'snapshot',return_value={}),patch.object(checks.subprocess,'run',return_value=subprocess.CompletedProcess([],0,b'',b'')) as executed:
   receipt,_=checks.execute(store,self_runtime.BACKEND,workspace,'qualified-image')
  self.assertTrue(receipt['passed'])
  mount='type=bind,source='+str(installed)+',target=/supervisor-runtime-gate.py,readonly'
  for call in executed.call_args_list:
   args=call.args[0];self.assertIn(mount,args);self.assertLess(args.index(mount),args.index('qualified-image'))

if __name__=='__main__':unittest.main()
