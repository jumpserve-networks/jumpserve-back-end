"""Credential-free completion regressions; real files, helper and Python tests.

The Docker launcher and persistence are fixtures. This suite makes no live model
calls and does not establish end-to-end Linux/OpenClaw qualification.
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import experiment_builder as builder
import generated_package
import repair_validation as validation
import store
from workflow import canonical,digest,numerical_protocol
from test_native_repair_handoff import context
from check_native_repair_handoff_linux import qualification_token

ORIGINAL=b'def convert(value):\n    return value\n'
REPAIRED=ORIGINAL.replace(b'return value',b'return value * 2')
TESTS=b'import unittest,experiment\nclass RepairChecks(unittest.TestCase):\n def test_original_failure(self):\n  with self.assertRaises(TypeError): experiment.convert(None)\n def test_corrected_behavior(self):\n  self.assertEqual(experiment.convert(7),14)\n'


def prospective():
    protocol=numerical_protocol('a'*64,'converted','count',0,[dict(identity='software-control',details=dict(test_config=dict(mode='emulated',arguments=[],parameters={})))])
    protocol.update(stage='pilot',data_origin='simulation',prior_exposure='Exposed software control; no scientific result.',planned_units=1,
        metrics=[dict(name='converted',units='count',planned_observations=1)],
        execution=dict(version='network-execution-v1',runner='built-package-v1',replications=1,limits=dict(wall_seconds=60,max_instances=1,max_output_bytes=32000,max_network_bytes=1024)))
    protocol['resource_limits'].update(wall_seconds=60,max_observations=1)
    return dict(protocol=protocol,experiment_type='independent-check')


class RepairCompletion(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.ctx=context()
        self.ctx['native_repair_handoff']=builder.repair_handoff(self.ctx)

    def bootstrap(self):
        return builder.bootstrap(dict(system=[dict(text='Preserve original scientific criteria.')],builder=dict(context=self.ctx)),self.root,self.root,[])

    def finish(self,*args):
        return subprocess.run([sys.executable,str(self.root/'finish-repair.py'),*args],capture_output=True)

    def test_isolated_qualification_token_cannot_refresh_or_use_expiring_credentials(self):
        path=self.root/'qualification-token.json';value=dict(access_token='test-only-'+'x'*100,expires_at=time.time()+600)
        path.write_bytes(canonical(value));path.chmod(0o600)
        self.assertEqual(qualification_token(path),value['access_token'])
        for invalid in (dict(value,expires_at=time.time()+30),dict(value,refresh_token='never-copy-a-refresh-grant')):
            path.write_bytes(canonical(invalid))
            with self.assertRaises(ValueError):qualification_token(path)
        path.write_bytes(canonical(value));path.chmod(0o644)
        with self.assertRaises(ValueError):qualification_token(path)
        path.chmod(0o600);link=self.root/'credential-link';link.symlink_to(path)
        with self.assertRaises(ValueError):qualification_token(link)

    def test_missing_metadata_is_server_delivered_without_fake_implementation_progress(self):
        before=copy.deepcopy(self.ctx);text,manifest=self.bootstrap()
        plan=self.root/'repair-test-plan.json';raw=plan.read_bytes()
        self.assertEqual(json.loads(raw),dict(version=validation.VERSION,incident_sha256=self.ctx['repair_packet']['incident_sha256'],scope='software-development-only'))
        self.assertTrue(manifest['repair_metadata']['created_by_server'])
        self.assertEqual(manifest['repair_metadata']['files'],[dict(path=plan.name,bytes=len(raw),sha256=digest(raw))])
        helper=next(f for f in manifest['files'] if f['path']=='finish-repair.py')
        self.assertEqual(helper['sha256'],digest((self.root/helper['path']).read_bytes()))
        self.assertEqual(builder.work_progress(self.root,self.ctx,[])['implementation_files'],[])
        self.assertIn('finish-repair.py',text);self.assertLessEqual(len(text.encode()),builder.BOOTSTRAP_BYTES)
        packet=json.loads((self.root/'work-packet.json').read_bytes())
        self.assertLessEqual(len(canonical(packet)),builder.WORK_PACKET_BYTES)
        self.assertEqual(packet['repair_handoff']['metadata']['status'],'valid')
        self.assertEqual(self.ctx,before)
        _,second=self.bootstrap();self.assertFalse(second['repair_metadata']['created_by_server'])
        self.assertEqual(plan.read_bytes(),raw)

    def test_existing_metadata_is_never_silently_rewritten(self):
        self.root.joinpath('project').mkdir()
        plan=self.root/'project/repair-test-plan.json'
        for raw in (b'{broken',canonical(dict(version=validation.VERSION,incident_sha256='0'*64,scope='software-development-only'))):
            plan.write_bytes(raw);_,manifest=self.bootstrap()
            self.assertEqual(plan.read_bytes(),raw)
            self.assertFalse((self.root/'repair-test-plan.json').exists())
            self.assertEqual(manifest['repair_metadata']['status'],'needs-correction')
        expected=dict(version=validation.VERSION,incident_sha256=self.ctx['repair_packet']['incident_sha256'],scope='software-development-only')
        plan.write_text(json.dumps(expected,indent=2));original=plan.read_bytes()
        self.assertEqual(self.bootstrap()[1]['repair_metadata']['status'],'valid')
        self.assertEqual(plan.read_bytes(),original)
        (self.root/'repair-test-plan.json').write_bytes(canonical(expected))
        self.assertEqual(self.bootstrap()[1]['repair_metadata']['status'],'needs-correction')
        self.assertEqual(plan.read_bytes(),original)

    def test_dense_repair_context_keeps_required_completion_and_trusted_instructions(self):
        self.ctx.update(claim={'description':'Original scientific criterion '*1000},builder_feedback='Keep the completed source '*1000,
            builder_recovery_plan={'action':'Prior repair evidence '*1000},runner_contracts={'netem-iperf-v1':{'output_contract':{'full':'Original output contract '*1000}}},
            proposal_contract={'full':'Prospective schema '*1000},source_material={'full':'Exact paper text '*1000},resource_authorization={'scope':'unchanged'},
            remaining_commitment_cents=0,deadline_at='2030-01-01T00:00:00Z')
        before=canonical(self.ctx);text,_=self.bootstrap();packet=json.loads((self.root/'work-packet.json').read_bytes())
        self.assertLessEqual(len(canonical(packet)),builder.WORK_PACKET_BYTES)
        self.assertEqual(packet['limits']['remaining_commitment_cents'],0)
        self.assertIn('finish-repair.py',packet['next_action'])
        self.assertEqual(packet['repair_handoff']['metadata']['status'],'valid')
        self.assertIn('Preserve original scientific criteria.',text)
        self.assertEqual((self.root/'research-context.json').read_bytes(),before)

    def test_staging_requires_explicit_choice_and_preserves_candidate_bytes(self):
        self.bootstrap();raw=json.dumps(prospective(),indent=2).encode()+b'\n'
        source=self.root/'selected.json';source.write_bytes(raw)
        self.assertNotEqual(self.finish().returncode,0)
        self.assertFalse((self.root/'candidate.json').exists())
        done=self.finish('selected.json');self.assertEqual(done.returncode,0,done.stderr)
        self.assertEqual((self.root/'candidate.json').read_bytes(),raw)
        self.assertEqual(json.loads(done.stdout)['status'],'candidate-staged;server-validation-and-fresh-reviews-pending')
        self.assertEqual(self.finish('selected.json').returncode,0)
        source.write_bytes(canonical(dict(protocol={},experiment_type='different')))
        self.assertNotEqual(self.finish('selected.json').returncode,0)
        self.assertEqual((self.root/'candidate.json').read_bytes(),raw)

    def test_unsafe_inputs_do_not_stage_candidates_or_modify_originals(self):
        self.bootstrap();source=self.root/'selected.json';raw=canonical(prospective());source.write_bytes(raw)
        outside=self.root.parent/(self.root.name+'-outside.json');outside.write_bytes(raw);self.addCleanup(outside.unlink)
        self.root.joinpath('link.json').symlink_to(source)
        self.root.joinpath('parent-link').symlink_to(self.root,target_is_directory=True)
        os.link(source,self.root/'hardlink.json')
        for name in ('link.json','parent-link/selected.json','hardlink.json',str(outside),'../'+outside.name):
            with self.subTest(name=name):self.assertNotEqual(self.finish(name).returncode,0)
        self.assertFalse((self.root/'candidate.json').exists());self.assertEqual(source.read_bytes(),raw)
        self.root.joinpath('hardlink.json').unlink()
        for invalid in (b'[]',b'{"protocol":{}}',b'x'*64001):
            source.write_bytes(invalid);self.assertNotEqual(self.finish('selected.json').returncode,0)
        source.write_bytes(raw);plan=self.root/'repair-test-plan.json';original=plan.read_bytes()
        plan.unlink();plan.symlink_to(source)
        self.assertNotEqual(self.finish('selected.json').returncode,0)
        with self.assertRaisesRegex(ValueError,'symlink'):self.bootstrap()
        self.assertEqual(source.read_bytes(),raw)
        plan.unlink();plan.write_bytes(original)
        (self.root/'candidate.json').symlink_to(source)
        self.assertNotEqual(self.finish('selected.json').returncode,0);self.assertEqual(source.read_bytes(),raw)

    def test_missing_conflicting_or_wrong_incident_plan_cannot_stage_candidate(self):
        self.bootstrap();(self.root/'selected.json').write_bytes(canonical(prospective()))
        plan=self.root/'repair-test-plan.json';raw=plan.read_bytes();plan.unlink()
        self.assertNotEqual(self.finish('selected.json').returncode,0)
        plan.write_bytes(raw.replace(self.ctx['repair_packet']['incident_sha256'].encode(),b'0'*64))
        self.assertNotEqual(self.finish('selected.json').returncode,0)
        plan.write_bytes(raw);self.root.joinpath('project').mkdir()
        other=self.root/'project/repair-test-plan.json';other.write_text(json.dumps(json.loads(raw),indent=2))
        self.assertNotEqual(self.finish('selected.json').returncode,0)
        self.assertFalse((self.root/'candidate.json').exists())

    def test_repaired_source_completes_validation_and_compilation_without_manual_metadata(self):
        project=self.root/'project';project.mkdir()
        files={'experiment.py':ORIGINAL,'repair_test.py':TESTS,'requirements.lock':b'','system-packages.lock.json':b'[]',
            'build-evidence.json':b'{"commands":[],"checks":[],"limitations":["Software control only."]}'}
        for name,raw in files.items():project.joinpath(name).write_bytes(raw)
        baseline=builder.checkpoint(self.root);self.ctx['builder_checkpoint']['sha256']=digest(baseline)
        self.ctx.pop('native_repair_handoff');self.ctx['native_repair_handoff']=builder.repair_handoff(self.ctx)
        (project/'experiment.py').write_bytes(REPAIRED)
        self.bootstrap();raw=canonical(prospective());self.root.joinpath('selected.json').write_bytes(raw)
        self.assertEqual(self.finish('selected.json').returncode,0)
        job=dict(id=str(uuid.uuid4()),study_id=str(uuid.uuid4()),lease_token=str(uuid.uuid4()))
        retained={}
        def retain(job,name,raw,*args):
            retained[name]=raw;return dict(artifact_id=str(uuid.uuid4()),sha256=digest(raw),version=builder.VERSION)
        actual_popen=subprocess.Popen
        def local_runner(command,**kwargs):
            # Only this fixed fixture runner is translated; production still uses Docker.
            self.assertEqual(command[-3:],['-I','-c',validation.RUNNER])
            mount=command[command.index('--mount')+1];cwd=mount.split('src=',1)[1].split(',dst=',1)[0]
            return actual_popen([sys.executable,'-I','-c',validation.RUNNER],cwd=cwd,**kwargs)
        with patch.object(store,'artifact_bytes',return_value=baseline),patch.object(validation.subprocess,'Popen',side_effect=local_runner),patch.dict(os.environ,OPENCLAW_BUILDER_IMAGE='sha256:'+'f'*64):
            evidence=validation.validate(job,self.root,self.ctx,retain)
            report=json.loads(retained['repair-validation.json'])
            self.assertTrue(report['passed']);self.assertEqual(report['test_result']['tests_run'],2)
            self.assertTrue(report['source_change']['changed']);self.assertEqual(evidence['sha256'],digest(retained['repair-validation.json']))
            self.assertEqual((project/'repair_test.py').read_bytes(),TESTS)
            with patch.object(builder,'retain',side_effect=retain),patch.object(store,'rpc',return_value={}):
                candidate=builder.freeze_candidate(job,self.root)
            compiled=next(iter(candidate['protocol']['configurations']))['details']['test_config']
            self.assertEqual(compiled['private_inputs']['project.zip']['sha256'],report['project_sha256'])
            self.assertEqual(generated_package.members(retained['project.zip'])['experiment.py'],REPAIRED)
            self.assertEqual(self.root.joinpath('candidate.json').read_bytes(),raw)
            # Staging supplies no pass: the same source tests can still fail.
            (project/'experiment.py').write_bytes(b'def convert(value):\n    return value * 3\n')
            with self.assertRaises(validation.RepairCheckFailure):validation.validate(job,self.root,self.ctx,retain)
            self.assertFalse(json.loads(retained['repair-validation.json'])['passed'])
            (project/'experiment.py').write_bytes(ORIGINAL)
            with self.assertRaisesRegex(ValueError,'unchanged'):validation.validate(job,self.root,self.ctx,retain)


if __name__=='__main__':unittest.main()
