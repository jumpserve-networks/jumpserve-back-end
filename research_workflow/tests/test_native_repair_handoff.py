"""Exposed handoff regressions; persistence/provider fixtures, real local files/checks."""
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch,Mock
import uuid
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import autonomy
import autonomy_model as model
import experiment_builder as builder
import repair_validation
import store
import autonomous_repair as repair
from workflow import canonical,digest


def context():
    incident=dict(reason='Correct the retained conversion failure.',stage='building')
    return dict(repair_directive='Implement this recorded repair with native tools.',
        repair_packet=dict(incident=incident,incident_sha256=digest(canonical(incident))),
        builder_checkpoint=dict(artifact_id=str(uuid.uuid4()),sha256='a'*64),
        builder_feedback='Fix the actual conversion and execute its incident-bound tests.',
        decision_lock='original-frozen-criterion')


def task(stage='planning'):
    return dict(id=str(uuid.uuid4()),study_id=str(uuid.uuid4()),claim_id=str(uuid.uuid4()),
        stage=stage,attempts=270,context=context(),workflow=dict(max_spend_cents=None,
        committed_cents=100000,deadline_at='2030-01-01T00:00:00Z'))


class NativeRepairHandoff(unittest.TestCase):
    def test_repair_packet_exposes_exact_metadata_object_and_current_parse_failure(self):
        ctx=context();ctx['native_repair_handoff']=builder.repair_handoff(ctx)
        ctx['builder_feedback']='Source checks already passed; finish the incident metadata and candidate.'
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'project').mkdir();(root/'project/experiment.py').write_text('def convert(value): return value * 2\n')
            plan=root/'repair-test-plan.json';plan.write_text('{\\"version\\":\\"repair-development-validation-v1\\"}')
            packet=builder.work_packet(ctx,builder.work_progress(root,ctx,[]),root)
            self.assertEqual(packet['repair_handoff']['metadata']['status'],'needs-correction')
            self.assertEqual(packet['feedback'],ctx['builder_feedback'])
            expected=dict(version=repair_validation.VERSION,incident_sha256=ctx['repair_packet']['incident_sha256'],scope='software-development-only')
            self.assertEqual(packet['repair_handoff']['test_plan'],expected)
            plan.write_bytes(canonical(expected));packet=builder.work_packet(ctx,builder.work_progress(root,ctx,[]),root)
            self.assertEqual(packet['repair_handoff']['metadata']['status'],'valid')
            self.assertIn('Metadata validity is not executed validation',packet['next_action'])
    def test_planning_repair_is_handed_off_before_any_model_admission(self):
        t=task();before=copy.deepcopy(t)
        with patch.object(builder,'enabled',return_value=True),patch.object(model,'OPENCLAW',True),patch.object(model,'call',side_effect=AssertionError('Planning model must not receive an implementation repair')) as call,patch.object(autonomy,'advance') as advance:
            autonomy.step(t)
        call.assert_not_called();saved=advance.call_args.kwargs['context']
        self.assertEqual(advance.call_args.kwargs['stage'],'building')
        self.assertEqual(saved['native_repair_handoff']['baseline_checkpoint'],before['context']['builder_checkpoint'])
        self.assertEqual(saved['decision_lock'],'original-frozen-criterion')
        self.assertEqual(saved['builder_checkpoint'],before['context']['builder_checkpoint'])
        self.assertEqual(t,before)

    def test_native_repair_stagnation_stays_native_but_known_failed_recovery_still_stops(self):
        t=task('building');t['context']['native_repair_handoff']=builder.repair_handoff(t['context'])
        fingerprint='a'*64;t['context']['builder_work_progress']=dict(sha256=fingerprint,unchanged_slices=2)
        answer=dict(id=str(uuid.uuid4()),status='complete',output=builder.decision(),runtime=dict(build_state='checkpoint',work_progress=dict(version='builder-work-progress-v2',implementation_sha256=fingerprint)))
        with patch.object(autonomy,'build_context',return_value={}),patch.object(model,'call',return_value=answer),patch.object(autonomy,'advance') as advance:
            autonomy.step(t)
        saved=advance.call_args.kwargs['context']
        self.assertEqual(advance.call_args.kwargs['stage'],'building')
        self.assertEqual(saved['builder_work_progress']['unchanged_slices'],3)
        t['context']=saved
        with patch.object(autonomy,'build_context',return_value={}),patch.object(model,'call',return_value=answer),patch.object(autonomy,'advance') as advance:
            autonomy.step(t)
        self.assertEqual(advance.call_args.args[1],'blocked')
        self.assertIn('builder_stall',advance.call_args.kwargs['context'])
        self.assertEqual(t['attempts'],270)

    def test_sibling_package_does_not_divert_unfinished_native_repair(self):
        t=task('building');t['context'].update(source_id=str(uuid.uuid4()),native_repair_handoff=builder.repair_handoff(t['context']))
        answer=dict(id=str(uuid.uuid4()),status='complete',output=builder.decision(),runtime={})
        with patch.object(builder,'catalog',return_value=[dict(id=str(uuid.uuid4()),task_id=str(uuid.uuid4()))]),patch.object(autonomy,'build_context',return_value={}),patch.object(model,'call',return_value=answer) as call,patch.object(autonomy,'advance') as advance:
            autonomy.step(t)
        call.assert_called_once();self.assertEqual(advance.call_args.kwargs['stage'],'building')

    def test_misrouted_request_is_rejected_before_prompt_lookup_or_begin(self):
        t=task();ctx=dict(t['context'],task='planning')
        with patch.object(model,'load_prompt') as prompt:
            control=Mock()
            with self.assertRaisesRegex(ValueError,'native.*building'):model.call(t,ctx,control)
            prompt.assert_not_called();control.assert_not_called()

    def test_native_tool_schema_requires_actual_edit_and_command_tools(self):
        ctx=context();good=dict(tools=[dict(type='function',name=n) for n in builder.TOOLS])
        builder.validate_tool_surface(good,ctx)
        for omitted in ('write','edit','exec'):
            bad=dict(tools=[t for t in good['tools'] if t['name']!=omitted])
            with self.subTest(omitted=omitted),self.assertRaisesRegex(ValueError,'required native repair tools'):builder.validate_tool_surface(bad,ctx)
        with self.assertRaisesRegex(ValueError,'allowlist'):builder.validate_tool_surface(dict(tools=[dict(type='function',name='arbitrary-tool')]),ctx)

    def test_exact_baseline_and_real_source_edit_are_required(self):
        t=task();original=b'def convert(x):\n return x\n'
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:z.writestr('project/experiment.py',original)
        raw=stream.getvalue();ctx=t['context'];ctx['builder_checkpoint']['sha256']=digest(raw)
        ctx['native_repair_handoff']=builder.repair_handoff(ctx)
        def sealed(source):
            out=io.BytesIO()
            with zipfile.ZipFile(out,'w') as z:
                for name,raw in {'experiment.py':source,'requirements.lock':b'','system-packages.lock.json':b'[]','build-evidence.json':b'{"commands":[],"checks":[],"limitations":[]}'}.items():z.writestr(name,raw)
            return out.getvalue()
        with patch.object(store,'artifact_bytes',return_value=raw):
            with self.assertRaisesRegex(ValueError,'experiment.py is unchanged'):repair_validation.source_change(t,ctx,sealed(original))
            changed=repair_validation.source_change(t,ctx,sealed(b'def convert(x):\n return x*2\n'))
            self.assertTrue(changed['changed']);self.assertEqual(changed['original_experiment_sha256'],digest(original))
        with patch.object(store,'artifact_bytes',return_value=raw+b' '):
            with self.assertRaisesRegex(ValueError,'baseline.*hash'):repair_validation.source_change(t,ctx,sealed(original))

    def test_missing_old_checkpoint_freezes_actual_existing_source_once(self):
        t=task();ctx=t['context'];ctx.pop('builder_checkpoint');before=copy.deepcopy(ctx)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'project').mkdir();(root/'project/experiment.py').write_text('def original(): return 7\n')
            captured=[]
            def retain(job,name,raw,*args):
                captured.append(raw);return dict(artifact_id=str(uuid.uuid4()),sha256=digest(raw))
            with patch.object(builder,'retain',side_effect=retain):
                handoff=builder.bind_repair_baseline(ctx,t,root)
                (root/'project/experiment.py').write_text('def original(): return 8\n')
                second=builder.bind_repair_baseline(dict(ctx,native_repair_handoff=handoff),t,root)
            self.assertEqual(second,handoff);self.assertEqual(len(captured),1)
            with zipfile.ZipFile(io.BytesIO(captured[0])) as archive:self.assertEqual(archive.read('project/experiment.py'),b'def original(): return 7\n')
            self.assertEqual(ctx,before)

    def test_budget_stopped_repair_reaches_fresh_review_only_after_exact_validation(self):
        t=task('building');ctx=t['context'];ctx['native_repair_handoff']=builder.repair_handoff(ctx)
        ctx.update(repair_progress=dict(state='budget'),repair_validation=dict(artifact_id=str(uuid.uuid4()),sha256='b'*64),
            proposal=dict(protocol=dict(configurations=[dict(details=dict(test_config=dict(private_inputs={'project.zip':dict(sha256='c'*64)})))])))
        report=dict(passed=True,incident_sha256=ctx['repair_packet']['incident_sha256'],project_sha256='c'*64,
            source_change=dict(changed=True,baseline_checkpoint=ctx['native_repair_handoff']['baseline_checkpoint']))
        before=copy.deepcopy(ctx)
        with patch.object(repair,'enabled',return_value=True),patch.object(repair,'original',return_value=canonical(report)):
            routed=repair.route(t,'queued',ctx,'comprehension','Fresh scientific review pending.')
        self.assertNotIn('repair_directive',routed['context']);self.assertNotIn('repair_packet',routed['context'])
        self.assertNotIn('native_repair_handoff',routed['context'])
        self.assertEqual(routed['context']['repair_progress']['state'],'budget')
        self.assertEqual(routed['context']['native_repair_completion']['status'],'validated-candidate;fresh-scientific-reviews-pending')
        self.assertEqual(ctx,before)
        for changed in (dict(report,passed=False),dict(report,project_sha256='d'*64),dict(report,source_change=dict(changed=False)),dict(report,incident_sha256='d'*64)):
            with patch.object(repair,'enabled',return_value=True),patch.object(repair,'original',return_value=canonical(changed)):
                with self.assertRaisesRegex(ValueError,'exact incident'):repair.route(t,'queued',ctx,'comprehension','Pending')

    def test_handoff_identity_cannot_be_reused_for_another_incident(self):
        ctx=context();ctx['native_repair_handoff']=builder.repair_handoff(ctx)
        ctx['repair_packet']['incident_sha256']='b'*64
        with self.assertRaisesRegex(ValueError,'identity differs'):builder.repair_handoff(ctx)

    def test_disabled_native_runtime_does_not_dispatch_to_planner(self):
        t=task()
        with patch.object(builder,'enabled',return_value=False),patch.object(model,'call') as call,patch.object(autonomy,'advance') as advance:
            autonomy.step(t)
        call.assert_not_called();self.assertEqual(advance.call_args.args[1],'blocked')
        self.assertTrue(advance.call_args.args[0]['_repair_external'])

    def test_terminal_controls_do_not_require_or_trigger_candidate_validation(self):
        t=task('building');ctx=t['context'];ctx.update(native_repair_handoff=builder.repair_handoff(ctx),repair_progress=dict(state='budget'),proposal={'retained':'incomplete'})
        for status in ('cancelled','budget','deadline'):
            with patch.object(repair,'enabled',return_value=True),patch.object(repair,'original') as original:
                self.assertIsNone(repair.route(t,status,ctx,'comprehension','Original control retained.'))
                original.assert_not_called()


if __name__=='__main__':unittest.main()
