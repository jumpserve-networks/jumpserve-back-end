"""Capability requests, real tool delivery and qualified self-update routing."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import Mock,patch

from platform_supervisor import capabilities, self_runtime, source
from platform_supervisor.common import canonical,digest,Hold
from platform_supervisor.worker import Controller


def specification():
    return dict(version=capabilities.VERSION,id='json-count',minimum_revision=1,
                objective='Provide a deterministic tool to count JSON array records.',
                acceptance=['Count the actual supplied array; reject a non-array without changing input.'])


def pack():
    return dict(version=capabilities.PACK,id='json-count',revision=1,
        description='Count actual JSON array records.',instructions='Invoke python3 count.py with a JSON array on stdin.',
        files={'count.py':'import json,sys\nvalue=json.load(sys.stdin)\nif not isinstance(value,list): raise ValueError("Array required")\nprint(json.dumps({"records":len(value)}))\n'})


class Store:
    def __init__(self,root):
        self.spool=root;self.worker=str(uuid.uuid4())
        self.task=dict(id=str(uuid.uuid4()),lease_token=str(uuid.uuid4()),repositories=[],
                      objective='Continue an assessment',deadline_at='2099-01-01T00:00:00Z',max_reserved_cents=-1,checkpoint={})
        self.flags=dict(paused=False,cancel_requested=False,deadline_at=self.task['deadline_at'],
                        committed_cents=0,max_reserved_cents=-1,model_calls=0,max_model_calls=-1,
                        capability_contract=capabilities.VERSION)
        self.manifest=dict(files={},source_revision='a'*40);self.context=None;self.calls=[];self.artifacts={}
        self.packet=None;self.originals={}
    def request(self,path,method='GET',body=None,raw=False):
        if path.endswith('/platform_supervisor_runtime'):return self.manifest
        if path.endswith('/platform_repair_context'):return self.packet
        if path.startswith('/rest/v1/research_artifacts?'):
            ident=path.split('id=eq.')[1].split('&')[0]
            return [self.originals[ident][0]] if ident in self.originals else []
        if path.startswith('/storage/v1/object/research-workflow-raw/'):
            return next(raw for row,raw in self.originals.values() if path.endswith(row['storage_path']))
        assert path.endswith('/platform_capability_worker')
        self.calls.append(copy.deepcopy(body))
        if body['p_operation']=='context':return self.context
        if body['p_operation']=='request':return dict(id=body['p_payload']['id'],implementation_task_id='22222222-2222-4222-8222-222222222222',available=False)
        if body['p_operation']=='activate':return dict(available=True,parent_resumed=True)
        raise AssertionError('Unexpected operation')
    def artifact(self,name,raw):
        sha=digest(raw);ident=str(uuid.uuid5(uuid.UUID(self.task['id']),sha))
        value=dict(id=ident,sha256=sha,storage_path=self.task['id']+'/'+sha)
        self.artifacts[name]=(raw,value);return value
    def rpc(self,operation,payload=None):
        if operation=='lease':return self.flags
        return {'recorded':True}
    def rows(self,*args,**kwargs):return []
    def checkpoint(self,status,reason,state):self.outcome=(status,reason,copy.deepcopy(state))


class Capabilities(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.store=Store(self.root)
        self.controller=Controller(self.store,Mock(),'synthetic-image')
        self.installed=self.root/'installed';self.installed.mkdir()
    def install_fixture(self,value=None):
        value=value or pack();name=capabilities.module_name(value['id'])
        raw=('CAPABILITY = '+repr(value)+'\n').encode()
        (self.installed/name).write_bytes(raw)
        self.store.manifest['files'][name]=digest(raw)
        return name,raw
    def test_verified_pack_delivers_a_real_usable_tool_without_importing_candidate_code(self):
        name,raw=self.install_fixture();workspace=self.root/'workspace';workspace.mkdir()
        original_inventory=capabilities.inventory
        with patch.object(capabilities,'inventory',side_effect=lambda store:original_inventory(store,self.installed)):
            delivered=capabilities.deliver(self.controller,workspace)
        path=workspace/delivered['installed'][0]['files'][0]['path']
        result=subprocess.run([sys.executable,str(path)],input=b'[1,2,3,4]',capture_output=True,check=True)
        self.assertEqual(json.loads(result.stdout),{'records':4})
        rejected=subprocess.run([sys.executable,str(path)],input=b'{}',capture_output=True)
        self.assertNotEqual(rejected.returncode,0)
        self.assertEqual(digest(raw),delivered['installed'][0]['module_sha256'])
        self.assertNotIn('credential',path.read_text())
    def test_registry_requires_installed_hash_and_does_not_execute_module_statements(self):
        name,raw=self.install_fixture()
        (self.installed/name).write_bytes(raw+b'\nraise RuntimeError("must not execute during inspection")\n')
        self.store.manifest['files'][name]=digest((self.installed/name).read_bytes())
        self.assertEqual(capabilities.inventory(self.store,self.installed)[0]['id'],'json-count')
        (self.installed/name).write_bytes(raw)
        with self.assertRaises(Hold):capabilities.inventory(self.store,self.installed)
    def test_literal_contract_rejects_path_escape_credentials_and_executable_metadata(self):
        for mutation in (dict(files={'../escape.py':'x'}),dict(files={'/absolute.py':'x'}),
                         dict(files={'.env.json':'x'}),dict(files={'tool.py':'sb_secret_never_publish'}),
                         dict(revision=True),dict(id='other-pack')):
            value=pack();value.update(mutation)
            with self.assertRaises(ValueError):capabilities.descriptor(('CAPABILITY='+repr(value)).encode(),'capability_json_count.py')
        with self.assertRaises(ValueError):capabilities.descriptor(b'CAPABILITY=dict()', 'capability_json_count.py')
    def test_request_retains_exact_original_and_uses_stable_durable_identity(self):
        workspace=self.root/'workspace';workspace.mkdir()
        raw=json.dumps(specification(),indent=2).encode();(workspace/capabilities.REQUEST).write_bytes(raw)
        for _ in range(2):
            with self.assertRaisesRegex(Hold,'queued automatically'):capabilities.request(self.controller,workspace)
        first,last=self.store.calls
        self.assertEqual(first,last)
        self.assertEqual(self.store.artifacts[capabilities.REQUEST][0],raw)
        self.assertEqual(first['p_token'],self.store.task['lease_token'])
        self.assertNotIn('owner_approval',first['p_payload'])
    def test_actual_controller_requests_capability_before_blocked_progress_can_stop_it(self):
        self.store.task['repositories']=[capabilities.BACKEND]
        def originals(workspace):
            (workspace/'jumpserve-back-end').mkdir()
            self.controller.state['sources']={capabilities.BACKEND:dict(revision='a'*40,snapshot={})}
        def execute(store,workspace,*args):
            (workspace/capabilities.REQUEST).write_bytes(canonical(specification()))
            (workspace/'progress.json').write_bytes(canonical(dict(version='platform-progress-v1',status='blocked',ready=False,remaining_blocker='Missing capability')))
            return dict(ready=False,feedback='Missing capability')
        with patch('platform_supervisor.worker.native.containers',return_value=[]),patch('platform_supervisor.worker.native.reconcile'),\
             patch.object(self.controller,'reconcile_pending'),patch.object(self.controller,'originals',side_effect=originals),\
             patch('platform_supervisor.worker.source_refresh.refresh'),patch('platform_supervisor.worker.repair.deliver'),\
             patch('platform_supervisor.worker.working_evidence.deliver'),patch('platform_supervisor.worker.research_runtime.reviewed'),\
             patch('platform_supervisor.worker.documentation.deliver'),patch('platform_supervisor.worker.checks.prepare_dependencies',return_value=None),\
             patch('platform_supervisor.worker.native.execute',side_effect=execute),\
             patch('platform_supervisor.worker.repair.observe') as observed:
            self.controller.run()
        observed.assert_not_called()
        self.assertEqual(self.store.outcome[0],'blocked',self.store.artifacts.get('supervisor-failure-original.json'))
        self.assertIn('implementation_task_id',self.store.outcome[2]['capability_wait'])
        self.assertIn('working-checkpoint.zip',self.store.artifacts)
    def test_self_runtime_gate_accepts_declared_new_pack_and_keeps_authority_frozen(self):
        previous=self.root/'previous/platform_supervisor';candidate=self.root/'candidate/platform_supervisor'
        shutil.copytree(Path(self_runtime.__file__).parent,previous,ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(previous,candidate)
        gate=self_runtime.gate();manifest=dict(version=self_runtime.VERSION,source_revision='a'*40,files=gate.files(previous))
        name=capabilities.module_name('json-count');(candidate/name).write_text('CAPABILITY='+repr(pack())+'\n')
        baseline=source.snapshot(previous.parent);scope=gate.inspect(previous,candidate,manifest)
        frozen=source.freeze(baseline,candidate.parent,supervisor_scope=scope)
        self.assertEqual([f['path'] for f in frozen['files']],['platform_supervisor/'+name])
        invalid=pack();invalid['files']={'../escape.py':'x'};(candidate/name).write_text('CAPABILITY='+repr(invalid))
        with self.assertRaises(ValueError):gate.inspect(previous,candidate,manifest)
    def test_task_cannot_complete_capability_installation_from_a_proposal(self):
        self.controller.state['capability_implementation']=dict(id=str(uuid.uuid4()),spec=specification())
        with self.assertRaises(Hold):capabilities.validate_candidate(self.controller,self.root)
        with self.assertRaises(Hold):capabilities.activate(self.controller)
        self.assertFalse(any(c['p_operation']=='activate' for c in self.store.calls))
    def test_a_declared_capability_without_a_new_executed_regression_cannot_enter_qualification(self):
        self.controller.state['capability_implementation']=dict(id=str(uuid.uuid4()),spec=specification())
        candidate=self.root/'candidate';folder=candidate/'platform_supervisor';(folder/'tests').mkdir(parents=True)
        (folder/capabilities.module_name('json-count')).write_text('CAPABILITY='+repr(pack()))
        with self.assertRaisesRegex(Hold,'regression'):capabilities.validate_candidate(self.controller,candidate)
        (folder/'tests/test_new_count.py').write_text('import unittest\nclass Count(unittest.TestCase):\n def test_input(self):\n  self.assertEqual(len([1,2]),2)\n')
        capabilities.validate_candidate(self.controller,candidate)
    def test_verified_activation_retires_only_the_bound_local_stop_and_preserves_original_bytes(self):
        workspace=self.root/'workspace';workspace.mkdir()
        progress=canonical(dict(version='platform-progress-v1',ready=False,status='blocked',remaining_blocker='Missing JSON count tool'))
        (workspace/'progress.json').write_bytes(progress)
        original=canonical(specification());(workspace/capabilities.REQUEST).write_bytes(original)
        activation=str(uuid.uuid4());self.controller.state['capability_activation']=activation
        installed=[dict(id='json-count',revision=1,module_sha256='b'*64)]
        def read(path,*args,**kwargs):
            if '/platform_capability_requests?' in path:return [dict(id=activation,capability_id='json-count',minimum_revision=1)]
            if '/platform_capability_activations?' in path:return [dict(module_sha256='b'*64,revision=1)]
            raise AssertionError('Unexpected read')
        with patch.object(self.store,'request',side_effect=read):
            capabilities.resume_progress(self.controller,workspace,[])  # No installed proof, no stop reset.
            self.assertEqual((workspace/'progress.json').read_bytes(),progress)
            capabilities.resume_progress(self.controller,workspace,installed)
            self.assertFalse((workspace/'progress.json').exists())
            self.assertFalse((workspace/capabilities.REQUEST).exists())
            (workspace/'progress.json').write_bytes(b'new unrelated blocker')
            capabilities.resume_progress(self.controller,workspace,installed)
        self.assertEqual((workspace/'progress.json').read_bytes(),b'new unrelated blocker')
        self.assertIn(progress,[v[0] for v in self.store.artifacts.values()])
        self.assertIn(original,[v[0] for v in self.store.artifacts.values()])
    def test_installed_collector_receives_only_task_bound_hash_verified_originals_and_redacts_its_view(self):
        name,raw=self.install_fixture();ident,study=str(uuid.uuid4()),str(uuid.uuid4())
        original=b'["a","b","c"]';sha=digest(original)
        self.store.originals[ident]=(dict(id=ident,sha256=sha,byte_count=len(original),storage_path=study+'/'+sha),original)
        self.store.task['assessment_repair']=True
        self.store.packet=dict(platform_task_id=self.store.task['id'],assessment_id=study,
            native_calls=[dict(request_artifact_id=ident,request_sha256=sha)])
        code=raw+b'\nimport json\ndef collect(context):\n return {"records":len(json.loads(context.original("'+ident.encode()+b'"))),"authorization":"Bearer synthetic-private-value"}\n'
        (self.installed/name).write_bytes(code);self.store.manifest['files'][name]=digest(code)
        workspace=self.root/'workspace';workspace.mkdir()
        with patch.object(capabilities,'__file__',str(self.installed/'capabilities.py')):
            index=capabilities.deliver(self.controller,workspace)
        view=(workspace/index['installed'][0]['diagnostics']['path']).read_bytes()
        self.assertNotIn(b'synthetic-private-value',view)
        self.assertEqual(json.loads(view)['view']['records'],3)
        self.assertIn(b'synthetic-private-value',self.store.artifacts['capability-diagnostics-json-count.json'][0])
        context=capabilities.Evidence(self.store,self.store.packet)
        with self.assertRaises(ValueError):context.original(str(uuid.uuid4()))
        self.store.originals[ident]=(self.store.originals[ident][0],b'changed bytes')
        with self.assertRaises(ValueError):context.original(ident)
    def test_collector_fault_automatically_requests_the_next_revision_without_exposing_private_exception_text(self):
        name,raw=self.install_fixture()
        code=raw+b'\ndef collect(context):\n raise ValueError("api_key=synthetic-private-value")\n'
        (self.installed/name).write_bytes(code);self.store.manifest['files'][name]=digest(code)
        workspace=self.root/'workspace';workspace.mkdir()
        with patch.object(capabilities,'__file__',str(self.installed/'capabilities.py')):
            with self.assertRaises(capabilities.CapabilityFault):capabilities.deliver(self.controller,workspace)
        request=json.loads((workspace/capabilities.REQUEST).read_bytes())
        self.assertEqual(request['minimum_revision'],2)
        self.assertNotIn('synthetic-private-value',request['objective'])
        self.assertIn(b'synthetic-private-value',self.store.artifacts['capability-collector-failure-json-count.json'][0])
        with self.assertRaisesRegex(Hold,'queued automatically'):capabilities.request(self.controller,workspace)


if __name__=='__main__':unittest.main()
