"""Development regressions for observed stalls; no live models or measurements."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import experiment_builder as builder
import autonomy
import autonomous_repair as repair
import autonomy_costs
import autonomy_inputs
import netem_iperf
import network_spec
import source_project
from workflow import canonical,digest
from test_network_execution import document
from test_executable_methods import sweep_config


class WorkPackets(unittest.TestCase):
    def test_packet_retrieves_exact_source_page_without_reading_entire_context(self):
        paper=dict(pages=[dict(page=1,text='Introduction'),dict(page=2,text='Methods: α, zero and missing remain distinct.')])
        context=dict(claim=dict(description='Original full claim'),builder_feedback='Implement the receiver byte check.',proposal_contract='Large protocol '*4000,source_material=dict(text_artifact_sha256=digest(canonical(paper))))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'paper-text.json').write_bytes(canonical(paper))
            text,manifest=builder.bootstrap(dict(system=[dict(text='Trusted original instruction')],builder=dict(context=context)),root,root,[])
            packet=json.loads((root/'work-packet.json').read_bytes())
            self.assertLessEqual(len(canonical(packet)),builder.WORK_PACKET_BYTES)
            self.assertEqual(packet['execution_contract'],builder.execution_contract())
            self.assertEqual(packet['feedback'],context['builder_feedback'])
            self.assertEqual(json.loads((root/'research-context.json').read_bytes()),context)
            self.assertIn('Trusted original instruction',text)
            read=subprocess.check_output([sys.executable,str(root/'read-retained.py'),'paper-page','2','0'])
            self.assertEqual(json.loads(json.loads(read)['fragment']),paper['pages'][1])
            self.assertEqual(json.loads(read)['sha256'],digest(canonical(paper['pages'][1])))
            self.assertIn('work-packet.json',[r['path'] for r in manifest['files']])
            index=json.loads((root/'context-index.json').read_bytes());proof=json.loads((root/'delivery-proofs.json').read_bytes())
            self.assertEqual(digest((root/'delivery-proofs.json').read_bytes()),index['proofs']['sha256'])
            self.assertEqual(proof['field_hashes']['claim'],digest(canonical(context['claim'])))
            (root/'paper-text.json').write_text('{"pages":[]}')
            with self.assertRaisesRegex(ValueError,'retained original'):builder.bootstrap(dict(system=[],builder=dict(context=context)),root,root,[])

    def test_continuation_does_not_recursively_quote_context_indexes(self):
        context={f'field-{i}':'retained source '*100 for i in range(35)}
        history=[dict(type='function_call',name='read',call_id='call-1',arguments='{"path":"context-index.json"}'),dict(type='function_call_output',call_id='call-1',output='recursive-metadata '*2000)]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);builder.bootstrap(dict(system=[],builder=dict(context=context)),root,root,history)
            continuation=(root/'builder-continuation.json').read_bytes()
            self.assertLess(len(continuation),1500)
            self.assertNotIn(b'recursive-metadata',continuation)
            self.assertEqual(builder.load_history(root),history)
            self.assertNotEqual(builder.work_progress(root,context,history)['sha256'],None)
            self.assertEqual(builder.work_progress(root,context,history)['implementation_files'],[])

    def test_context_checkpoint_notice_does_not_replace_actionable_feedback(self):
        task=dict(id=str(uuid.uuid4()),study_id=str(uuid.uuid4()),claim_id=str(uuid.uuid4()),stage='building',context=dict(builder_feedback='Fix the exact receiver-byte conversion.'),attempts=7)
        answer=dict(id=str(uuid.uuid4()),status='complete',output=builder.decision(),runtime=dict(build_state='checkpoint',context_checkpoint=True,scheduling_feedback='Context window ended; all original bytes retained.'))
        with patch.object(autonomy,'build_context',return_value={}),patch.object(autonomy.model,'call',return_value=answer),patch.object(autonomy,'advance') as advance:
            autonomy.step(task)
        context=advance.call_args.kwargs['context']
        self.assertEqual(context['builder_feedback'],task['context']['builder_feedback'])
        self.assertIn('Context window ended',context['builder_scheduling_feedback'])
        self.assertEqual(task['attempts'],7)

    def test_packet_handles_legacy_scheduling_feedback_and_exposes_existing_observation_mapping(self):
        contract=netem_iperf.observation_contract()
        context=dict(builder_feedback='Native context boundary reached before provider admission.',runner_contracts={'netem-iperf-v1':{'output_contract':contract}},builder_recovery_plan=dict(action='Write the missing receiver-byte instrumentation.'))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);inventory=builder.work_progress(root,context,[]);packet=builder.work_packet(context,inventory,root)
            self.assertEqual(packet['feedback'],context['builder_recovery_plan']['action'])
            self.assertEqual(packet['available_netem_output_contract'],contract)
            self.assertIn('Legacy scheduling',packet['feedback_scope'])

    def test_repeated_stagnation_stays_blocked_through_real_advance_and_repair_route(self):
        fingerprint='a'*64
        key=digest(canonical(dict(implementation=fingerprint,adapter=builder.recovery_identity())))
        ref=dict(id=str(uuid.uuid4()),version=repair.VERSION,state='repairing',category='implementation',revision=1,updated_at='2026-10-01T00:00:00Z')
        task=dict(id=str(uuid.uuid4()),study_id=str(uuid.uuid4()),claim_id=str(uuid.uuid4()),stage='building',attempts=100,context=dict(repair_progress=ref,builder_work_progress=dict(sha256=fingerprint,unchanged_slices=3),builder_stalled_recovery_keys=[key]))
        answer=dict(id=str(uuid.uuid4()),status='complete',output=builder.decision(),runtime=dict(work_progress=dict(version='builder-work-progress-v2',implementation_sha256=fingerprint)))
        record=dict(ref,evidence={})
        def transition(t,r,state,reason,evidence):return dict(r,state=state,reason=reason,evidence=evidence)
        with patch.object(autonomy,'build_context',return_value={}),patch.object(autonomy.model,'call',return_value=answer),patch.object(autonomy,'control',return_value={}) as control,patch.object(repair,'enabled',return_value=True),patch.object(repair,'rows',return_value=[record]),patch.object(repair,'transition',side_effect=transition):
            autonomy.step(task)
        payload=control.call_args.args[2]
        self.assertEqual(payload['status'],'blocked')
        self.assertEqual(payload['context']['repair_progress']['state'],'blocked')
        self.assertEqual(payload['context']['builder_stall']['recovery_key'],key)
        self.assertEqual(task['attempts'],100)


class CounterEnvelope(unittest.TestCase):
    def full_grid(self):
        p=document();p['execution']['runner']='netem-iperf-v1';p['execution']['limits']=dict(wall_seconds=600,max_instances=1,max_output_bytes=1000000,max_network_bytes=18760000000)
        p['execution']['replications']=3;p['planned_units']=3
        p['configurations'][0]['details']['test_config']=sweep_config();p['metrics']=[dict(name=k,units=v,planned_observations=5) for k,v in netem_iperf.METRICS.items()];p['resource_limits'].update(wall_seconds=600,max_observations=25)
        return p

    def test_exact_full_grid_compiles_without_altering_scientific_configuration(self):
        p=self.full_grid();before=copy.deepcopy(p)
        specs=network_spec.specs(p,verify_hashes=False)
        self.assertEqual([s['replication'] for s in specs],[0,1,2])
        self.assertEqual(specs[0]['config']['loss_levels_pct'],[0,1,2,5,10])
        self.assertEqual(specs[0]['config']['duration_seconds'],30)
        self.assertEqual(netem_iperf.network_budget(specs[0]['config']),18760000000)
        self.assertEqual(p,before)
        p['execution']['limits']['max_network_bytes']=netem_iperf.MAX_COUNTER_BYTES+1
        with self.assertRaises(ValueError):network_spec.specs(p,verify_hashes=False)
        p['execution']['limits']['max_network_bytes']=18759999999
        with self.assertRaisesRegex(ValueError,'interface-counter'):network_spec.specs(p,verify_hashes=False)
        other=document('custom-package-v1');other['execution']['limits']['max_network_bytes']=10000000001
        with self.assertRaises(ValueError):network_spec.specs(other,verify_hashes=False)

    def test_new_counter_policy_requires_qualification_and_exact_immutable_hash(self):
        spec=network_spec.specs(self.full_grid(),verify_hashes=False)[0]
        policy=json.loads(autonomy_costs.NETEM_POLICY_PATH.read_bytes())
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'policy.json';policy['approved_for_lab_execution']=False;path.write_bytes(canonical(policy))
            with patch.object(autonomy_costs,'NETEM_POLICY_PATH',path):
                with self.assertRaisesRegex(ValueError,'operational qualification'):autonomy_costs.builtin_quote(canonical(spec))
            policy['approved_for_lab_execution']=True;path.write_bytes(canonical(policy))
            env=dict(NETWORK_NETEM_IPERF_FUNCTION='arn:aws:lambda:us-east-1:395567831870:function:Fixture:1',NETWORK_NETEM_IPERF_POLICY_SHA256=digest(canonical(policy)))
            with patch.object(autonomy_costs,'NETEM_POLICY_PATH',path),patch.dict(os.environ,env):
                quote=autonomy_costs.builtin_quote(canonical(spec));self.assertEqual(quote['cents'],100)
                spec['limits']['max_network_bytes']=20000000001
                with self.assertRaisesRegex(ValueError,'counter/output'):autonomy_costs.builtin_quote(canonical(spec))
            with patch.object(autonomy_costs,'NETEM_POLICY_PATH',path),patch.dict(os.environ,dict(env,NETWORK_NETEM_IPERF_POLICY_SHA256='0'*64)):
                with self.assertRaisesRegex(ValueError,'matching server-owned'):autonomy_costs.builtin_quote(canonical(network_spec.specs(self.full_grid(),verify_hashes=False)[0]))
        contract=autonomy_inputs.runner_contracts()['netem-iperf-v1']
        self.assertEqual(contract['counter_envelope']['maximum_bytes'],20000000000)


class SourceSealing(unittest.TestCase):
    def project(self, root):
        for name, raw in {'experiment.py':b'print(1)\n','requirements.lock':b'','system-packages.lock.json':b'[]','build-evidence.json':b'{"commands":[],"checks":[],"limitations":[]}'}.items():
            (root/name).write_bytes(raw)

    def test_actual_compiler_cache_is_retained_without_entering_executable_zip(self):
        import py_compile
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.project(root);py_compile.compile(str(root/'experiment.py'),doraise=True)
            original={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()};retained={}
            def retain(job,name,raw):
                retained[name]=raw;return dict(artifact_id=str(uuid.uuid4()),sha256=digest(raw))
            result=source_project.freeze({},root,retain)
            self.assertEqual(set(__import__('generated_package').members(result)),set(original)-{n for n in original if '__pycache__' in n})
            self.assertEqual({str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()},original)
            manifest=json.loads(next(raw for n,raw in retained.items() if n.startswith('source-project-')))
            self.assertEqual(manifest['package_sha256'],digest(result))
            self.assertEqual(len(manifest['excluded']),1)
            entry=manifest['excluded'][0];self.assertEqual(original[entry['path']],retained['python-cache-'+entry['sha256']+'.bin'])

    def test_dependency_symlink_and_noncache_source_guards_remain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.project(root);cache=root/'__pycache__';cache.mkdir();(cache/'source.py').write_text('print(1)')
            with self.assertRaisesRegex(ValueError,'Only derived'):source_project.freeze({},root,None)
            (cache/'source.py').unlink();(root/'.venv').mkdir();(root/'.venv'/'metadata.txt').write_text('installed')
            with self.assertRaisesRegex(ValueError,'dependency'):source_project.freeze({},root,None)
            (root/'.venv'/'metadata.txt').unlink();(root/'link.py').symlink_to(root/'experiment.py')
            with self.assertRaisesRegex(ValueError,'symlinks'):source_project.freeze({},root,None)


if __name__=='__main__':unittest.main()
