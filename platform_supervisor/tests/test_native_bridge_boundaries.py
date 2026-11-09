"""Local native bridge with synthetic provider bytes; no Docker or cloud calls."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock,patch
from platform_supervisor import native
from platform_supervisor.common import canonical,digest,Hold
from platform_supervisor.tests.test_call_recovery import Store


class NativeBridgeBoundaries(unittest.TestCase):
    def execute(self,mode='success',cleanup=None,retention_failure=False):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);store=Store(root);store.run=None;workspace=root/'workspace';workspace.mkdir()
            calls=[];clock=[0];response=io.BytesIO(b'Original retained provider bytes');response.status=200
            transport=Mock(return_value=response)
            original_rpc=store.rpc
            def rpc(operation,payload=None):
                if operation=='begin-run' and mode=='budget':return {'denied':'budget'}
                return original_rpc(operation,payload)
            store.rpc=rpc
            if retention_failure:
                original_artifact=store.artifact
                def artifact(name,*args,**kwargs):
                    if name.startswith('cleanup-failure-'):raise OSError('Original artifact store unavailable')
                    return original_artifact(name,*args,**kwargs)
                store.artifact=artifact
            def stream(response,sink,*args):
                sink.write(response.read());return {'status':'completed','output':[]}
            def cli(command,**kwargs):
                if mode=='slice':clock[0]=native.POLICY['slice_seconds']
                config=json.loads(Path(command[command.index('--config')+1]).read_bytes());provider=config['models']['providers']['lab']
                body=store.request
                if mode=='wrong-model':body=canonical(dict(json.loads(body),model='foreign'))
                elif mode=='wrong-tools':body=canonical(dict(json.loads(body),tools=[{'type':'function','name':'unapproved-host-command'}]))
                elif mode=='oversized':body=b''
                if mode=='blocked':(workspace/'progress.json').write_bytes(canonical({'version':'platform-progress-v1','ready':False,'status':'blocked','remaining_blocker':'Original missing prerequisite'}))
                for _ in range(2):
                    headers={'Authorization':'Bearer '+provider['apiKey']}
                    if mode=='wrong-auth':headers['Authorization']='Bearer foreign'
                    if mode=='oversized':headers['Content-Length']='4000001'
                    request=urllib.request.Request(provider['baseUrl']+('/other' if mode=='wrong-path' else '/responses'),data=body,headers=headers)
                    try:
                        with urllib.request.urlopen(request,timeout=5) as result:calls.append((result.status,result.read()))
                    except urllib.error.HTTPError as error:calls.append((error.code,error.read()))
                if mode=='success':(workspace/'proposal.json').write_bytes(canonical({'version':'platform-proposal-v1','ready':True,'summary':'Original synthetic proposal'}))
                return subprocess.CompletedProcess(command,0,canonical({'status':'ok'}),b'')
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(native.runtime,'verify_runtime'))
                stack.enter_context(patch.object(native.time,'monotonic',side_effect=lambda:clock[0]))
                stack.enter_context(patch.object(native.builder,'builder_package',return_value=Path('/synthetic-fixed-package')))
                stack.enter_context(patch.object(native.subprocess,'run',side_effect=cli))
                stack.enter_context(patch.object(native,'containers',return_value=[]))
                stack.enter_context(patch.object(native,'normalize',side_effect=cleanup))
                stack.enter_context(patch.object(native.wire,'read_stream',side_effect=stream))
                stack.enter_context(patch.object(native.wire,'usage',return_value={}))
                stack.enter_context(patch.object(native.builder,'validate_calls',side_effect=ValueError('Original invalid tool timeout') if mode=='invalid-tool' else None))
                token_source=Mock(side_effect=['synthetic-token',OSError('Original credential storage unavailable')]) if mode=='credential-failure' else lambda:'synthetic-token'
                result=native.execute(store,workspace,root/'slice','sha256:'+'a'*64,token_source=token_source,transport=transport)
            return result,store,calls,transport.call_count

    def test_identical_successful_native_requests_reuse_original_bytes_without_another_provider_call(self):
        result,store,calls,count=self.execute()
        self.assertTrue(result['ready']);self.assertEqual(result['summary'],'Original synthetic proposal');self.assertEqual(calls,[(200,b'Original retained provider bytes')]*2);self.assertEqual(count,1);self.assertEqual(store.run['status'],'complete')

    def test_retained_running_and_complete_responses_reconcile_without_another_provider_request(self):
        with tempfile.TemporaryDirectory() as root:
            store=Store(root);store.run['status']='running';native.reconcile(store);self.assertEqual(store.calls[-1][0],'finish-run');self.assertEqual(store.run['status'],'unknown')
            store.receipt['status']='complete';store.write();store.rpc=Mock();completed={'original':'completed response'}
            with patch.object(native.wire,'read_stream',return_value=completed),patch.object(native.builder,'validate_calls') as validate:self.assertEqual(native.reconcile(store),[])
            validate.assert_called_once_with(completed);self.assertEqual(store.rpc.call_args.args[0],'reconcile-run');self.assertEqual(store.rpc.call_args.args[1]['proof']['request_sha256'],digest(store.request))
            store.receipt.update(status='unknown',http_status=None);store.write()
            with self.assertRaisesRegex(Hold,'remains unresolved'):native.reconcile(store)

    def test_hash_matching_but_unreadable_original_requests_are_not_accepted_as_http_reconciliation_proof(self):
        with tempfile.TemporaryDirectory() as root:
            store=Store(root);request=b'Original non-JSON request';store.run['request_sha256']=digest(request);store.receipt['request_sha256']=digest(request)
            with self.assertRaisesRegex(Hold,'request cannot be verified'):native.call_recovery.http_failure(store.run,store.receipt,request,store.response)

    def test_invalid_model_tool_surface_and_request_bounds_never_reach_the_provider(self):
        for mode in ['wrong-model','wrong-tools','oversized','blocked','wrong-auth','wrong-path']:
            with self.subTest(mode=mode):
                result,store,calls,count=self.execute(mode)
                self.assertFalse(result['ready']);self.assertEqual(count,0);self.assertIsNone(store.run);self.assertTrue(all(code==400 for code,_ in calls))
                if mode=='blocked':self.assertTrue(result['blocked'])

    def test_credential_failure_before_transport_retains_a_failed_call_without_an_invented_response(self):
        result,store,calls,count=self.execute('credential-failure');self.assertFalse(result['ready']);self.assertEqual(count,0);self.assertEqual(store.run['status'],'failed');self.assertTrue(all(code==400 for code,_ in calls))

    def test_missing_original_receipts_and_unpinned_images_stop_before_inference(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(directory);(store.spool/store.task['id']/'calls'/store.run['id']/'receipt.json').unlink()
            with self.assertRaisesRegex(Hold,'complete retained receipt'):native.reconcile(store)
            with patch.object(native.runtime,'verify_runtime') as runtime,self.assertRaisesRegex(Hold,'image digest'):native.execute(store,directory,Path(directory)/'slice','unqualified:latest')
            runtime.assert_not_called()

    def test_container_cleanup_refuses_excess_foreign_or_malformed_container_identities(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix='original-task-'
            for defect in ['excess','foreign','malformed','empty','unrelated']:
                records=canonical(dict(Id='a'*12,Name='/'+(prefix+'container' if defect=='foreign' else 'another-task'),Mounts=[dict(Type='bind',Source=str(Path(directory).parent/'foreign-workspace'))])).decode()
                output=['a'*12+'\n',records] if defect in ['foreign','unrelated'] else [' '.join(['a'*12]*257)] if defect=='excess' else ['not-a-container'] if defect=='malformed' else ['']
                with self.subTest(defect=defect),patch.object(native.subprocess,'check_output',side_effect=output),patch.object(native.subprocess,'run') as remove:
                    if defect in ['empty','unrelated']:self.assertEqual(native.containers(prefix,directory),[])
                    else:
                        with self.assertRaises(native.CleanupUnknown):native.containers(prefix,None if defect=='malformed' else directory)
                    remove.assert_not_called()

    def test_invalid_tool_proposals_are_failed_originals_and_cannot_be_replayed(self):
        result,store,calls,count=self.execute('invalid-tool')
        self.assertFalse(result['ready']);self.assertIn('Original invalid tool timeout',result['feedback']);self.assertEqual(count,1);self.assertEqual(store.run['status'],'failed');self.assertTrue(all(code==400 for code,_ in calls))

    def test_cleanup_failure_is_fenced_even_after_a_successful_original_model_response(self):
        failure=subprocess.CalledProcessError(1,['synthetic-normalization'],stderr=b'Original synthetic normalization failure')
        with self.assertRaises(native.CleanupUnknown) as caught:self.execute(cleanup=failure)
        self.assertEqual(caught.exception.status,'reconciling');self.assertNotIn('synthetic normalization failure',str(caught.exception))

    def test_budget_and_slice_admission_stops_never_dispatch_a_provider_request(self):
        for mode in ['budget','slice']:
            with self.subTest(mode=mode):
                result,store,calls,count=self.execute(mode);self.assertFalse(result['ready']);self.assertIsNone(store.run);self.assertEqual(count,0);self.assertTrue(all(code==400 for code,_ in calls))

    def test_failed_cleanup_artifact_persistence_still_keeps_host_admission_fenced(self):
        with self.assertRaises(native.CleanupUnknown) as caught:self.execute(cleanup=OSError('Original metadata failure'),retention_failure=True)
        self.assertEqual(caught.exception.status,'reconciling')

    def test_invalid_ready_proposals_do_not_become_publication_authority_and_normalization_uses_the_fixed_isolated_command(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);(path/'proposal.json').write_bytes(b'Original invalid JSON');self.assertFalse(native.readiness(path,{'status':'ok'})['ready'])
            with patch.object(native.subprocess,'run') as run:native.normalize(path,'sha256:'+'a'*64,'original-prefix-')
            command=run.call_args.args[0];self.assertIn('--network',command);self.assertEqual(command[command.index('--network')+1],'none');self.assertEqual(command[command.index('--user')+1],'0');self.assertEqual(run.call_args.kwargs['timeout'],45);self.assertTrue(run.call_args.kwargs['check'])
        with patch.object(native.subprocess,'check_output',return_value='') as inventory:self.assertEqual(native.containers('original-prefix-'),[])
        self.assertEqual(inventory.call_args.args[0],['docker','ps','-aq','--filter','name=^original-prefix-'])


if __name__=='__main__':unittest.main()
