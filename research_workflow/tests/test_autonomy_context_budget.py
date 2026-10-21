"""Exposed request-size and recovery checks; no provider or cloud calls."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import autonomy,autonomy_model as model,autonomy_evidence as evidence,workflow
from test_autonomy import retained_ipv6_contexts,task


class ContextBudget(unittest.TestCase):
 def large(self):
  context,_,_,_=retained_ipv6_contexts()
  # Make only selection descriptions larger; no original fixture is overwritten.
  for check in context['registered_checks']:
   check['protocol']['configurations']*=6
  context['previous_feedback']='Keep the original conditions and missing observations.'
  return context

 def test_oversize_selection_uses_exact_wire_budget_and_retains_every_check_identity(self):
  context=self.large();before=workflow.canonical(context)
  self.assertGreater(len(before),96000)
  request=model.request('Server policy '*1000,context)
  self.assertLessEqual(len(workflow.canonical(request)),96000)
  sent=json.loads(request['messages'][0]['content'][0]['text'])
  self.assertEqual(workflow.canonical(context),before)
  self.assertEqual(sent['context_transport']['original_context_sha256'],workflow.digest(before))
  self.assertEqual(len(sent['registered_checks']),len(context['registered_checks']))
  for original,index in zip(context['registered_checks'],sent['registered_checks']):
   self.assertEqual(index['id'],original['id'])
   self.assertEqual(index['selection_details']['sha256'],workflow.digest(workflow.canonical(original)))
   self.assertEqual(index['selection_details']['read_request'],dict(kind='check',id=original['id'],cursor=0))
  for key in set(context)-{'registered_checks'}:self.assertEqual(sent[key],context[key])

 def test_fitting_context_is_byte_for_byte_unchanged(self):
  context,_,_,_=retained_ipv6_contexts();request=model.request('Server policy',context)
  self.assertEqual(request['messages'][0]['content'][0]['text'],workflow.canonical(context).decode())
  self.assertNotIn('context_transport',json.loads(request['messages'][0]['content'][0]['text']))

 def test_compaction_does_not_waive_irreducible_context_or_system_limits(self):
  for context,system in ((self.large(),'System'*20000),({'task':'planning','initial_prompt':'x'*100000},'System')):
   with self.assertRaisesRegex(ValueError,'96 KB'):model.request(system,context)

 def test_referenced_large_check_is_readable_to_completion_and_bound_to_original_hash(self):
  t=task();t['context']['prepared_id']='prepared'
  job=dict(id=str(__import__('uuid').uuid4()))
  document=dict(protocol={'hypotheses':'Exact original claim','conditions':'\\"測定'*5000},id=job['id'])
  with patch.object(autonomy,'one',return_value={'source_id':'source'}),patch.object(autonomy,'inventory',return_value=[job]),patch.object(autonomy,'retained_jobs',return_value=[]),patch.object(autonomy,'check_context',return_value=document):
   fragments=[];cursor=0
   while True:
    result=autonomy.inspect_evidence(t,dict(kind='check',id=job['id'],cursor=cursor))['result']
    self.assertLessEqual(len(workflow.canonical(result)),evidence.READ_BYTES)
    self.assertEqual(result['document_sha256'],workflow.digest(workflow.canonical(document)))
    fragments.append(result['fragment']);cursor=result['next_cursor']
    if cursor is None:break
  self.assertEqual(''.join(fragments),workflow.canonical(document).decode())

 def test_recovery_preflights_without_provider_calls_or_resuming_oversized_tasks(self):
  t=task();t['status']='blocked';candidate=dict(id=t['id'],study_id=t['study_id'],owner_id='owner')
  for fits in (True,False):
   calls=[]
   def rpc(name,payload):
    calls.append((name,payload))
    return [candidate] if name=='research_autonomy_budget_candidates' else None if name=='research_autonomy_control' else {'status':'queued'}
   context={'task':'planning','initial_prompt':'x' if fits else 'x'*100000}
   with patch.dict(autonomy.os.environ,{'RESEARCH_AUTONOMY_CONTEXT_RECOVERY':model.CONTEXT_BUDGET_VERSION}),patch.object(model,'load_prompt',return_value=({},'System')),patch.object(autonomy,'one',return_value=copy.deepcopy(t)),patch.object(autonomy,'rows',return_value=[t['workflow']]),patch.object(autonomy,'build_context',return_value=context),patch.object(autonomy.store,'rpc',side_effect=rpc),patch.object(model,'invoke') as invoke:
    result=autonomy.handler({})
   invoke.assert_not_called()
   resumed=[p for name,p in calls if name=='research_autonomy_resume_budget']
   self.assertEqual(bool(resumed),fits)
   if fits:
    self.assertLessEqual(resumed[0]['p_request_bytes'],96000)
    self.assertEqual(resumed[0]['p_request_sha256'],workflow.digest(workflow.canonical(model.request('System',context))))
   else:self.assertEqual(result['context_recovery'][0]['status'],'retained-blocker')

if __name__=='__main__':unittest.main()
