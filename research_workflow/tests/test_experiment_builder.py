"""Builder development cases; no credentials, provider calls or cloud changes."""
import io,json,sys,tempfile,unittest,uuid,zipfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import experiment_builder as builder,generated_package as package,autonomy,autonomy_model,network_spec
import shared_campaigns
import autonomy_costs

def project(root):
    root.mkdir()
    for name,raw in {'experiment.py':b'print(1)\n','requirements.lock':b'','system-packages.lock.json':b'[]','build-evidence.json':b'{"commands":[],"checks":[],"limitations":[]}'}.items():(root/name).write_bytes(raw)

class Packages(unittest.TestCase):
    def test_standing_authority_is_hash_bound_and_does_not_claim_unavailable_capabilities(self):
        policy=builder.action_policy()
        self.assertEqual(policy['sha256'],autonomy.digest(builder.ACTION_POLICY_PATH.read_bytes()))
        actions={a['id']:a for a in policy['document']['actions']}
        self.assertEqual(actions['install-and-configure-software']['path'],'native-builder')
        self.assertEqual(actions['provision-and-configure-experiment']['path'],'reviewed-executor')
        self.assertIn('Authorization and technical availability are different',policy['document']['instruction'])
        self.assertIn('USD100',str(policy['document']['retained_controls']))
    def test_actual_admitted_builder_prompt_records_policy_identity_and_instruction(self):
        task=dict(id=str(uuid.uuid4()),study_id=str(uuid.uuid4()),stage='building',attempts=0,context={})
        events=[]
        def control(t,op,payload):events.append((op,payload));return dict(replayed=False)
        response=dict(stopReason='tool_use',output=dict(message=dict(role='assistant',content=[dict(toolUse=dict(name='submit_assessment_step',input=builder.decision()))])),usage={})
        with patch.object(autonomy_model,'OPENCLAW',True),patch.object(autonomy_model,'MODEL','gpt-6.1-sol'),patch.object(builder,'enabled',return_value=True),patch.object(autonomy_model,'load_prompt',return_value=({'id':str(uuid.uuid4())},'Synthetic reviewed builder fixture')):
            result=autonomy_model.call(task,{'task':'building'},control,lambda value:response)
        policy=builder.action_policy();value=events[0][1]['request']
        self.assertEqual(result['status'],'complete');self.assertEqual([op for op,_ in events],['begin','finish'])
        self.assertEqual(value['builder']['execution_authority'],{k:policy[k] for k in ('version','sha256')})
        self.assertIn(policy['document']['instruction'],[s['text'] for s in value['system']])
        self.assertEqual(events[0][1]['request_sha256'],autonomy.digest(events[0][1]['request_raw'].encode()))
    def test_policy_mismatch_stops_before_credential_or_provider_use(self):
        from unittest.mock import Mock
        token=Mock();transport=Mock();job=dict(id=str(uuid.uuid4()),task_id=str(uuid.uuid4()))
        logical=dict(builder=dict(context={},execution_authority=dict(version='experiment-action-policy-v1',sha256='0'*64)))
        with tempfile.TemporaryDirectory() as tmp,self.assertRaisesRegex(ValueError,'policies differ'):
            builder.execute(logical,Path(tmp)/'slice',job,token_source=token,transport=transport)
        token.assert_not_called();transport.assert_not_called()
    def test_unvalidated_cloud_policy_cannot_admit_a_generated_experiment(self):
        with self.assertRaisesRegex(ValueError,'awaits operational validation'):autonomy_costs.builtin_quote(b'{"runner":"built-package-v1"}')
    def test_frozen_project_is_deterministic_and_requires_actual_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'project';project(root);first=package.freeze(root)
            self.assertEqual(first,package.freeze(root));self.assertEqual(package.members(first)['experiment.py'],b'print(1)\n')
            (root/'requirements.lock').write_text('numpy>=1\n')
            with self.assertRaisesRegex(ValueError,'exact version'):package.freeze(root)
            (root/'requirements.lock').write_text('numpy==2.0.0 --hash=sha256:'+'a'*64+'\n');package.freeze(root)
    def test_traversal_duplicate_and_zip_bomb_rejected(self):
        for name,raw in [('../escape',b'bad'),('/escape',b'bad'),('huge',b'x'*256001)]:
            stream=io.BytesIO()
            with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:archive.writestr(name,raw)
            with self.assertRaises(ValueError):package.members(stream.getvalue())
    def test_checkpoint_round_trip_and_safety(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);a=root/'a';a.mkdir();(a/'actual.txt').write_text('42');raw=builder.checkpoint(a);b=root/'b';b.mkdir();builder.restore(raw,b)
            self.assertEqual((b/'actual.txt').read_text(),'42');(a/'link').symlink_to('/etc/passwd')
            with self.assertRaisesRegex(ValueError,'symlink'):builder.checkpoint(a)
    def test_native_config_is_pinned_sandboxed_and_has_no_fallback(self):
        config=builder.config(Path('/tmp/workspace'),Path('/tmp/state'),'http://127.0.0.1:1/v1','synthetic','sha256:'+'a'*64,'js-test-')
        self.assertEqual(config['models']['providers']['lab']['api'],'openai-responses')
        self.assertEqual(config['tools']['allow'],builder.TOOLS);self.assertEqual(config['tools']['exec']['host'],'sandbox');self.assertFalse(config['tools']['elevated']['enabled'])
        self.assertEqual(config['agents']['defaults']['sandbox']['workspaceAccess'],'rw');self.assertEqual(config['agents']['defaults']['model']['fallbacks'],[])
        with self.assertRaises(ValueError):builder.config(Path('/tmp/a'),Path('/tmp/b'),'url','token','ubuntu:latest','x')
    def test_only_implementation_blockers_enter_builder(self):
        task={'stage':'planning'}
        with patch.dict('os.environ',RESEARCH_BUILDER_ENABLED='true'):
            self.assertTrue(builder.eligible(task,{'blockers':[{'kind':'implementation'}]}))
            for kind in ('equipment','authorization','capacity','provider','design'):
                self.assertFalse(builder.eligible(task,{'blockers':[{'kind':'implementation'},{'kind':kind}]}))
    def test_controller_checkpoint_continues_original_claim_and_budget(self):
        task=dict(stage='building',id=str(uuid.uuid4()),study_id=str(uuid.uuid4()),claim_id=str(uuid.uuid4()),context={},attempts=3)
        answer=dict(id=str(uuid.uuid4()),status='complete',output=builder.decision(),runtime=dict(build_state='checkpoint',checkpoint={'sha256':'a'*64,'artifact_id':str(uuid.uuid4())},provider_calls=2))
        with patch.object(autonomy,'build_context',return_value={}),patch.object(autonomy.model,'call',return_value=answer),patch.object(autonomy,'advance') as advance:
            autonomy.step(task)
        self.assertEqual(advance.call_args.kwargs['stage'],'building');self.assertEqual(advance.call_args.args[1],'queued');self.assertEqual(advance.call_args.kwargs['context']['builder_checkpoint'],answer['runtime']['checkpoint'])
    def test_building_decisions_do_not_claim_scientific_coverage(self):
        value=builder.decision({'protocol':{},'experiment_type':'independent-check'})
        autonomy_model.validate_phase(value,{'task':'building'})
        value['coverage_complete']=True
        with self.assertRaises(ValueError):autonomy_model.validate_phase(value,{'task':'building'})
    def test_generated_host_escapes_and_unbounded_commands_are_rejected_before_execution(self):
        for arguments in ({'command':'id','host':'gateway'},{'command':'id','elevated':True},{'command':'id','timeoutSeconds':0},{'command':'id','timeoutSeconds':999}):
            with self.assertRaises(ValueError):builder.validate_calls({'output':[{'type':'function_call','name':'exec','arguments':json.dumps(arguments)}]})
        builder.validate_calls({'output':[{'type':'function_call','name':'exec','arguments':json.dumps({'command':'python3 experiment.py','timeoutSeconds':30})}]})
    def test_budget_stop_is_terminal_and_keeps_checkpoint(self):
        task=dict(stage='building',id=str(uuid.uuid4()),study_id=str(uuid.uuid4()),claim_id=str(uuid.uuid4()),context={},attempts=3)
        checkpoint={'artifact_id':str(uuid.uuid4()),'sha256':'a'*64}
        answer=dict(id=str(uuid.uuid4()),status='complete',output=builder.decision(),runtime=dict(admission_denied='budget',checkpoint=checkpoint))
        with patch.object(autonomy,'build_context',return_value={}),patch.object(autonomy.model,'call',return_value=answer),patch.object(autonomy,'advance') as advance:autonomy.step(task)
        self.assertEqual(advance.call_args.args[1],'budget');self.assertEqual(advance.call_args.kwargs['context']['builder_checkpoint'],checkpoint)
    def test_package_fragments_still_allow_inspection_with_full_protocol(self):
        self.assertIn('inspect',autonomy_model.request_actions({'task':'critic','candidate':{'protocol':{}},'candidate_package':{'representation':'retained-package-reference'}}))
    def test_external_builder_blocker_stops_without_spending_on_another_slice(self):
        task=dict(stage='building',id=str(uuid.uuid4()),study_id=str(uuid.uuid4()),claim_id=str(uuid.uuid4()),context={},attempts=3)
        answer=dict(id=str(uuid.uuid4()),status='complete',output=builder.decision(),runtime=dict(build_state='external-blocked'))
        with patch.object(autonomy,'build_context',return_value={}),patch.object(autonomy.model,'call',return_value=answer),patch.object(autonomy,'advance') as advance:autonomy.step(task)
        self.assertEqual(advance.call_args.args[1],'blocked')
    def test_retirement_storage_retry_preserves_one_original_receipt(self):
        task_id=str(uuid.uuid4());study=str(uuid.uuid4())
        def rows(kind,*args,**kwargs):
            return [dict(id=task_id,study_id=study,status='resolved')] if kind=='autonomy_tasks' else [dict(status='completed',deadline_at='2030-01-01T00:00:00Z')] if kind=='autonomy_workflows' else [dict(owner_id=str(uuid.uuid4()))]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'builder-workspaces'/task_id;root.mkdir(parents=True)
            with patch.object(builder.store,'rows',side_effect=rows),patch.object(builder,'container_action',return_value=['a'*12]) as cleanup,patch.object(builder.store,'artifact',return_value={'id':str(uuid.uuid4())}) as artifact,patch.object(builder.store,'append',side_effect=[builder.store.StoreError('Synthetic acknowledgment failure'),{}]):
                with self.assertRaises(builder.store.StoreError):builder.reap(tmp)
                original=(root/'retirement-pending.json').read_bytes();builder.reap(tmp)
            self.assertEqual(cleanup.call_count,1);self.assertEqual(artifact.call_args_list[0].args[2],artifact.call_args_list[1].args[2]);self.assertEqual((root/'retired.json').read_bytes(),original)
    def test_archived_plan_uses_authoritative_claim_source_without_a_text_binding(self):
        source=str(uuid.uuid4());task=dict(id=str(uuid.uuid4()),study_id=str(uuid.uuid4()),actor_id=str(uuid.uuid4()),claim_id=str(uuid.uuid4()),context={'prepared_id':str(uuid.uuid4())})
        with patch.object(shared_campaigns.store,'rows',return_value=[{'id':task['claim_id'],'source_id':source}]),patch.object(shared_campaigns,'identity',return_value=({},'a'*64)) as identity,patch.object(shared_campaigns.store,'rpc',return_value={}):
            shared_campaigns.register(task,{},[],str(uuid.uuid4()),str(uuid.uuid4()))
        self.assertEqual(identity.call_args.args[1],source)

if __name__=='__main__':unittest.main()
