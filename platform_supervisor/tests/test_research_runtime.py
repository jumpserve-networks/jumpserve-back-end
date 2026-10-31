import unittest
from unittest.mock import Mock
from platform_supervisor import research_runtime
from platform_supervisor.releases import Releases,plan
from platform_supervisor.common import Hold
class RuntimeAdapterTests(unittest.TestCase):
 def test_runtime_release_requires_qualified_scope_and_dedicated_workflow(self):
  repo=research_runtime.BACKEND;paths=['research_workflow/autonomy.py']
  with self.assertRaises(Hold):plan(repo,paths)
  scope=dict(version='research-controller-recovery-v1',scientific_and_request_content_unchanged=True,existing_tests_unchanged=True)
  self.assertEqual(plan(repo,paths,scope),'research-runtime')
  with self.assertRaises(Hold):plan(repo,paths+['benchmark.py'],scope)
  github=Mock();release=Releases(github);release.find=Mock(return_value=None);github.main.return_value='c'*40
  request=dict(repository=repo,revision='a'*40,pin_revision='b'*40,workflow_revision='c'*40,component='research-runtime')
  release.dispatch(dict(id='operation',request=request))
  self.assertEqual(github.post.call_args.args[1],'actions/workflows/research-runtime-release.yml/dispatches')
  self.assertEqual(github.post.call_args.args[2]['inputs'],dict(operation_id='operation',revision='a'*40,pin_revision='b'*40))
 def test_pin_verification_cannot_claim_another_production_revision(self):
  controller=Mock();controller.store.task=dict(id='11111111-1111-4111-8111-111111111111');controller.state=dict(changes=[dict(component='research-runtime-pin',merge_sha='b'*40)])
  with self.assertRaises(Hold):research_runtime.record_pin_release(controller,dict(pin_revision='a'*40))
  proof=dict(pin_revision='b'*40,revision='a'*40,operation_id='original',passed=True,source_binding='c'*64)
  derived=research_runtime.record_pin_release(controller,proof)
  self.assertEqual(derived['runtime_revision'],'a'*40);self.assertEqual(derived['revision'],'b'*40)
  self.assertEqual(proof['revision'],'a'*40)
if __name__=='__main__':unittest.main()
