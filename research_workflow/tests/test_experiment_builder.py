"""Builder development cases; no credentials, provider calls or cloud changes."""
import io,json,os,subprocess,sys,tempfile,unittest,uuid,zipfile
from pathlib import Path
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import experiment_builder as builder,generated_package as package,autonomy,autonomy_model,network_spec
import shared_campaigns
import autonomy_costs

def project(root):
    root.mkdir()
    for name,raw in {'experiment.py':b'print(1)\n','requirements.lock':b'','system-packages.lock.json':b'[]','build-evidence.json':b'{"commands":[],"checks":[],"limitations":[]}'}.items():(root/name).write_bytes(raw)

class Packages(unittest.TestCase):
    def test_large_context_and_history_are_exact_files_not_repeated_bootstrap_text(self):
        context=dict(task='building',claim={'id':str(uuid.uuid4()),'description':'Check the frozen source calculation.'},source_material='paper-evidence-α'*11000,runner_contracts='contract'*10000)
        history=[dict(type='function_call_output',call_id='prior-1',output='retained-tool-result-β'*2500)]
        logical=dict(system=[dict(text='Fixed test instructions.')],builder=dict(context=context))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);workspace=root/'workspace';workspace.mkdir()
            text,manifest=builder.bootstrap(logical,workspace,root,history)
            self.assertLessEqual(len(text.encode()),builder.BOOTSTRAP_BYTES)
            self.assertNotIn(context['source_material'],text);self.assertNotIn(history[0]['output'],text)
            self.assertEqual((workspace/'research-context.json').read_bytes(),autonomy.canonical(context))
            self.assertEqual((workspace/'native-tool-history.json').read_bytes(),autonomy.canonical(history))
            for record in manifest['files']:
                raw=(workspace/record['path']).read_bytes();self.assertEqual(autonomy.digest(raw),record['sha256'])
            fragments=[];page=0;reads=[]
            while page is not None:
                read=subprocess.run([sys.executable,str(workspace/'read-retained.py'),'context','source_material',str(page)],capture_output=True,check=True)
                value=json.loads(read.stdout);self.assertLessEqual(len(value['fragment'].encode()),6000)
                call_id='read-'+str(page)
                reads.extend([dict(type='function_call',call_id=call_id,name='exec',arguments=json.dumps({'command':'python3 /workspace/read-retained.py context source_material '+str(page)})),dict(type='function_call_output',call_id=call_id,output=read.stdout.decode())])
                fragments.append(value['fragment']);page=value['next_page']
            self.assertEqual(''.join(fragments).encode(),autonomy.canonical(context['source_material']))
            self.assertEqual(value['sha256'],autonomy.digest(''.join(fragments).encode()))
            pages=builder.delivered_pages(context,reads)
            self.assertEqual(sorted(pages['source_material']),list(range(len(fragments))))
            self.assertEqual(builder.delivered_pages(dict(context,source_material='changed original'),reads),{})
            altered=json.loads(reads[1]['output']);altered['fragment']='invented replacement';reads[1]['output']=json.dumps(altered)
            self.assertNotIn(0,builder.delivered_pages(context,reads)['source_material'])
            self.assertIn('native_output_sha256',pages['source_material'][0])
            # Later delivery changes only current workspace copies; the previous
            # immutable request is not edited and a new manifest identifies it.
            old=autonomy.canonical(logical);context2=dict(context,builder_feedback='Address the exact retained error.')
            builder.bootstrap(dict(logical,builder=dict(context=context2)),workspace,root,history)
            self.assertEqual(autonomy.canonical(logical),old)
            self.assertIn('not a review-completeness',manifest['scope'])

    def test_trusted_instructions_are_never_silently_truncated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with self.assertRaisesRegex(ValueError,'Trusted builder instructions'):
                builder.bootstrap(dict(system=[dict(text='x'*16001)],builder=dict(context={})),root,root,[])

    def test_whole_optional_fields_move_to_files_to_leave_room_for_trusted_rules(self):
        context={'task':'building','claim':{'description':'z'*2700},'builder_feedback':'f'*1400}
        instruction='Trusted system instruction. '*365
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            text,manifest=builder.bootstrap(dict(system=[{'text':instruction}],builder={'context':context}),root,root,[])
            self.assertLessEqual(len(text.encode()),builder.BOOTSTRAP_BYTES)
            self.assertIn(instruction,text)
            self.assertEqual(json.loads((root/'research-context.json').read_bytes()),context)
            self.assertNotEqual(set(manifest['inline_fields']),set(context))

    def test_invalid_execution_mode_is_retained_but_never_registered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);project(root/'project')
            candidate=dict(experiment_type='independent-check',protocol=dict(
                configurations=[dict(identity='loopback',details=dict(test_config=dict(mode='loopback',arguments=[],parameters={})))],
                execution=dict(version='network-execution-v1',runner='built-package-v1',replications=1,limits=dict(wall_seconds=60,max_instances=1,max_output_bytes=32000,max_network_bytes=1024)),
                resource_limits=dict(wall_seconds=60,max_observations=1),metrics=[dict(name='bytes',units='bytes',planned_observations=1)],input_versions=[],planned_units=1))
            original=json.dumps(candidate).encode();(root/'candidate.json').write_bytes(original)
            retained=[]
            def retain(job,name,raw,*args):
                retained.append(name);return dict(artifact_id=str(uuid.uuid4()),sha256=autonomy.digest(raw),version=builder.VERSION)
            with patch.object(builder,'retain',side_effect=retain),patch.object(builder.store,'rpc') as register:
                with self.assertRaisesRegex(ValueError,'mode, package, arguments and parameters'):builder.freeze_candidate({},root)
            self.assertEqual((root/'candidate.json').read_bytes(),original)
            self.assertIn('candidate-original.json',retained);self.assertNotIn('candidate-compiled.json',retained);register.assert_not_called()
    def test_slot_continuation_keeps_native_calls_and_results_without_repeating_history(self):
        call=dict(type='function_call',call_id='call-1',name='exec',arguments='{"command":"python3 --version"}')
        output=dict(type='function_call_output',call_id='call-1',output='Python 3.10.12')
        original=[call]
        resumed=builder.merge_tool_history(original,[dict(role='user',content='A fresh native session.'),call,output])
        self.assertEqual(original,[call]);self.assertEqual(resumed,[call,output])
        self.assertEqual(builder.merge_tool_history(resumed,[call,output]),resumed)
    def test_continuation_rejects_changed_tool_results_without_overwriting_original(self):
        original=[dict(type='function_call_output',call_id='call-1',output='connection refused')]
        with self.assertRaises(ValueError):builder.merge_tool_history(original,[dict(original[0],output='success')])
        self.assertEqual(original[0]['output'],'connection refused')
    def test_continuation_bound_does_not_silently_drop_earlier_evidence(self):
        original=[dict(type='function_call_output',call_id='call-1',output='x'*(builder.HISTORY_BYTES-100))]
        with self.assertRaises(ValueError):builder.merge_tool_history(original,[dict(type='function_call_output',call_id='call-2',output='y'*200)])
        self.assertEqual(len(original),1)
    def test_megabyte_history_round_trips_with_original_global_indices_and_bounded_reader(self):
        history=[dict(type='function_call_output',call_id='call-'+str(i),output='α retained '*1000) for i in range(130)]
        self.assertGreater(len(autonomy.canonical(history)),1000000)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);builder.save_history(root,history)
            self.assertEqual(builder.load_history(root),history)
            (root/'read-retained.py').write_text(builder.READ_RETAINED)
            read=subprocess.run([sys.executable,str(root/'read-retained.py'),'history','129','0'],capture_output=True,check=True)
            self.assertEqual(json.loads(read.stdout)['sha256'],autonomy.digest(autonomy.canonical(history[129])))
            restored=root/'restored';restored.mkdir();builder.restore(builder.checkpoint(root),restored)
            self.assertEqual(builder.load_history(restored),history)
            # A hash alone is not accepted without matching compressed bytes.
            (root/'native-tool-history.json.gz').write_bytes(b'altered')
            with self.assertRaisesRegex(ValueError,'hash differs'):builder.load_history(root)
    def test_delivery_and_notes_do_not_count_as_implementation_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);first=builder.work_progress(root,{},[])
            (root/'progress.json').write_text('{"claimed":"done"}')
            (root/'builder-continuation.json').write_text('{}')
            (root/'native-tool-history.json.gz').write_bytes(b'generated transport')
            (root/'notes.txt').write_text('Another status message')
            self.assertEqual(first['implementation_sha256'],builder.work_progress(root,{},[])['implementation_sha256'])
            (root/'experiment.py').write_text('print(42)')
            self.assertNotEqual(first['implementation_sha256'],builder.work_progress(root,{},[])['implementation_sha256'])
    def test_compressed_history_cannot_bypass_the_expanded_byte_limit(self):
        import gzip
        raw=b'x'*(builder.HISTORY_BYTES+1);compressed=gzip.compress(raw,mtime=0)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'native-tool-history.json.gz').write_bytes(compressed)
            (root/'native-tool-history.json').write_bytes(autonomy.canonical(dict(version='builder-history-gzip-v1',path='native-tool-history.json.gz',bytes=len(raw),records=1,sha256=autonomy.digest(raw),compressed_sha256=autonomy.digest(compressed))))
            with self.assertRaisesRegex(ValueError,'Expanded native history'):builder.load_history(root)
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
    def test_lost_checkpoint_ack_adopts_only_byte_identical_retained_workspace(self):
        for changed in (False,True):
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);job=dict(id=str(uuid.uuid4()),task_id=str(uuid.uuid4()),study_id=str(uuid.uuid4()))
                taskroot=root/'builder-workspaces'/job['task_id'];workspace=taskroot/'workspace';workspace.mkdir(parents=True)
                (workspace/'experiment.py').write_text('print(42)');raw=builder.checkpoint(workspace)
                prior=dict(artifact_id=str(uuid.uuid4()),sha256=autonomy.digest(raw));old=dict(artifact_id=str(uuid.uuid4()),sha256='a'*64)
                (taskroot/'checkpoint-reference.json').write_bytes(autonomy.canonical(old))
                if changed:(workspace/'experiment.py').write_text('print("unconfirmed change")')
                with patch.object(builder.store,'artifact_bytes',return_value=raw),patch.object(builder,'action_policy',side_effect=RuntimeError('Stop after pre-provider identity check')):
                    with self.assertRaisesRegex(ValueError if changed else RuntimeError,'needs reconciliation' if changed else 'Stop after pre-provider'):
                        builder.execute(dict(builder=dict(checkpoint=prior,context={})),root/'slice',job,token_source=Mock(),transport=Mock())
                self.assertEqual(json.loads((taskroot/'checkpoint-reference.json').read_bytes()),old if changed else prior)
    def test_unvalidated_cloud_policy_cannot_admit_a_generated_experiment(self):
        with patch.object(autonomy_costs,'BUILT_POLICY_PATH',Path(autonomy_costs.__file__).parent/'policies/built-package-costs-v1.json'):
            with self.assertRaisesRegex(ValueError,'awaits operational validation'):autonomy_costs.builtin_quote(b'{"runner":"built-package-v1"}')
    def test_lab_policy_quote_requires_exact_executor_policy_and_resource_envelope(self):
        policy=json.loads(autonomy_costs.BUILT_POLICY_PATH.read_bytes());spec=dict(runner='built-package-v1',config=dict(package=dict(sha256=policy['wrapper_sha256'])),limits=dict(max_instances=1,wall_seconds=1800,max_network_bytes=10000000000,max_output_bytes=3000000))
        env=dict(NETWORK_BUILT_PACKAGE_FUNCTION='arn:aws:lambda:us-east-1:395567831870:function:FixedLabExecutor:1',NETWORK_BUILT_PACKAGE_POLICY_SHA256=autonomy.digest(autonomy.canonical(policy)))
        with patch.dict(os.environ,env):
            quote=autonomy_costs.builtin_quote(autonomy.canonical(spec));self.assertEqual(quote['cents'],1000);self.assertIsNone(quote['quote']['measured_charges_usd']);self.assertFalse(quote['quote']['hard_resource_bounds_verified'])
            spec['limits']['max_instances']=2
            with self.assertRaisesRegex(ValueError,'exceeds'):autonomy_costs.builtin_quote(autonomy.canonical(spec))
        with patch.dict(os.environ,dict(env,NETWORK_BUILT_PACKAGE_POLICY_SHA256='0'*64)):
            with self.assertRaisesRegex(ValueError,'matching server-owned'):autonomy_costs.builtin_quote(autonomy.canonical(spec))
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
        self.assertEqual(config['agents']['defaults']['sandbox']['docker']['user'],str(os.getuid())+':'+str(os.getgid()))
        with self.assertRaises(ValueError):builder.config(Path('/tmp/a'),Path('/tmp/b'),'url','token','ubuntu:latest','x')
    def test_clean_executor_contract_identifies_file_paths_and_original_wrapper(self):
        contract=builder.execution_contract()
        self.assertEqual(contract['wrapper_sha256'],autonomy.digest(Path(package.__file__).read_bytes()))
        self.assertIn('NOT inline JSON',contract['environment']['JUMPSERVE_SPEC'])
        self.assertIn("Path(os.environ['JUMPSERVE_SPEC']).read_text",contract['spec_loader'])
        self.assertIn('sudo -n',contract['workspace_user'])
        self.assertEqual(contract['version'],'builder-execution-contract-v3')
        self.assertEqual(set(contract['spec_fields']['metrics'][0]),{'name','units'})
        self.assertIn('NOT present',contract['metric_rule'])
        self.assertEqual(contract['spec_fields']['config']['mode'],['emulated','real-world'])
    def test_recoverable_design_and_implementation_enter_builder_but_external_gates_do_not(self):
        task={'stage':'planning'}
        with patch.dict('os.environ',RESEARCH_BUILDER_ENABLED='true'):
            self.assertTrue(builder.eligible(task,{'blockers':[{'kind':'implementation'}]}))
            self.assertTrue(builder.eligible(task,{'blockers':[{'kind':'design'}]}))
            self.assertTrue(builder.eligible(task,{'blockers':[{'kind':'implementation'},{'kind':'design'}]}))
            for kind in ('equipment','authorization','capacity','provider'):
                self.assertFalse(builder.eligible(task,{'blockers':[{'kind':'implementation'},{'kind':kind}]}))
    def test_controller_checkpoint_continues_original_claim_and_budget(self):
        task=dict(stage='building',id=str(uuid.uuid4()),study_id=str(uuid.uuid4()),claim_id=str(uuid.uuid4()),context={},attempts=3)
        answer=dict(id=str(uuid.uuid4()),status='complete',output=builder.decision(),runtime=dict(build_state='checkpoint',checkpoint={'sha256':'a'*64,'artifact_id':str(uuid.uuid4())},provider_calls=2))
        with patch.object(autonomy,'build_context',return_value={}),patch.object(autonomy.model,'call',return_value=answer),patch.object(autonomy,'advance') as advance:
            autonomy.step(task)
        self.assertEqual(advance.call_args.kwargs['stage'],'building');self.assertEqual(advance.call_args.args[1],'queued');self.assertEqual(advance.call_args.kwargs['context']['builder_checkpoint'],answer['runtime']['checkpoint'])
    def test_read_only_changes_cannot_reset_the_controller_stagnation_counter(self):
        task=dict(stage='building',id=str(uuid.uuid4()),study_id=str(uuid.uuid4()),claim_id=str(uuid.uuid4()),context=dict(builder_work_progress=dict(sha256='implementation-original',unchanged_slices=2),builder_feedback='Implement the retained loss sweep next.'),attempts=70)
        answer=dict(id=str(uuid.uuid4()),status='complete',output=builder.decision(),runtime=dict(build_state='checkpoint',work_progress=dict(version='builder-work-progress-v2',sha256='changed-read-coverage',implementation_sha256='implementation-original',files=[],delivered={})))
        with patch.object(autonomy,'build_context',return_value={}),patch.object(autonomy.model,'call',return_value=answer),patch.object(autonomy,'advance') as advance:
            autonomy.step(task)
        saved=advance.call_args.kwargs['context'];self.assertEqual(saved['builder_work_progress']['unchanged_slices'],3)
        self.assertEqual(advance.call_args.kwargs['stage'],'planning');self.assertIn('Repeated context reads',saved['builder_feedback'])
    def test_sibling_package_returns_to_claim_specific_planning_without_resetting_checkpoint_or_criterion(self):
        source=str(uuid.uuid4());package_id=str(uuid.uuid4())
        task=dict(stage='building',id=str(uuid.uuid4()),study_id=str(uuid.uuid4()),context=dict(source_id=source,builder_checkpoint={'sha256':'a'*64},decision_lock='frozen',completed_job_ids=['earlier-result']))
        candidate=dict(id=package_id,task_id=str(uuid.uuid4()))
        with patch.object(builder,'catalog',return_value=[candidate]),patch.object(autonomy.model,'call') as inference,patch.object(autonomy,'advance') as advance:
            autonomy.step(task)
        inference.assert_not_called();saved=advance.call_args.kwargs['context']
        self.assertEqual(advance.call_args.kwargs['stage'],'planning');self.assertEqual(saved['decision_lock'],'frozen')
        self.assertEqual(saved['builder_checkpoint'],task['context']['builder_checkpoint']);self.assertEqual(saved['completed_job_ids'],['earlier-result'])
        self.assertEqual(saved['builder_reuse_considered'],[package_id]);self.assertNotIn('builder_reuse_considered',task['context'])
        # The same candidate cannot bounce a builder back to planning forever.
        task['context']=saved
        answer=dict(id=str(uuid.uuid4()),status='complete',output=builder.decision(),runtime={})
        with patch.object(builder,'catalog',return_value=[candidate]),patch.object(autonomy,'build_context',return_value={}),patch.object(autonomy.model,'call',return_value=answer) as inference,patch.object(autonomy,'advance') as advance:
            autonomy.step(task)
        inference.assert_called_once();self.assertEqual(advance.call_args.kwargs['stage'],'building')
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
    def test_resumed_retired_task_gets_new_cleanup_identity_without_overwriting_old_receipt(self):
        task_id=str(uuid.uuid4());study=str(uuid.uuid4())
        def rows(kind,*args,**kwargs):
            return [dict(id=task_id,study_id=study,status='resolved')] if kind=='autonomy_tasks' else [dict(status='completed',deadline_at='2030-01-01T00:00:00Z')] if kind=='autonomy_workflows' else [dict(owner_id=str(uuid.uuid4()))]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'builder-workspaces'/task_id;root.mkdir(parents=True)
            old=b'{"original":"retired before recovery"}'
            for name in ('retired.json','retirement-pending.json'):(root/name).write_bytes(old)
            with patch.object(builder.store,'rows',side_effect=rows),patch.object(builder,'container_action',side_effect=[['a'*12],['a'*12],[]]) as cleanup,patch.object(builder.store,'artifact',return_value={}) as artifact,patch.object(builder.store,'append'):
                builder.reap(tmp);new=(root/'retired.json').read_bytes();builder.reap(tmp)
            self.assertNotEqual(new,old);self.assertEqual((root/'retirement-history'/(autonomy.digest(old)+'.json')).read_bytes(),old)
            self.assertEqual([x.args[1] for x in cleanup.call_args_list],['list','remove','list'])
            self.assertEqual(artifact.call_count,1)
            self.assertEqual(artifact.call_args.args[3],str(uuid.uuid5(uuid.UUID(task_id),'builder-retirement:'+autonomy.digest(new))))
    def test_archived_plan_uses_authoritative_claim_source_without_a_text_binding(self):
        source=str(uuid.uuid4());task=dict(id=str(uuid.uuid4()),study_id=str(uuid.uuid4()),actor_id=str(uuid.uuid4()),claim_id=str(uuid.uuid4()),context={'prepared_id':str(uuid.uuid4())})
        with patch.object(shared_campaigns.store,'rows',return_value=[{'id':task['claim_id'],'source_id':source}]),patch.object(shared_campaigns,'identity',return_value=({},'a'*64)) as identity,patch.object(shared_campaigns.store,'rpc',return_value={}):
            shared_campaigns.register(task,{},[],str(uuid.uuid4()),str(uuid.uuid4()))
        self.assertEqual(identity.call_args.args[1],source)

if __name__=='__main__':unittest.main()
