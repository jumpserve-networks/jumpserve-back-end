"""Exposed, zero-provider-call regression against one immutable failed trial.

Runs the fixed, hash-checked conversion fixture with local Python instead of
Docker. Candidate selection is scripted, never attributed to the live model.
"""
import argparse
import datetime as dt
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch
import uuid
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import experiment_builder as builder
import generated_package
import openclaw_runtime as runtime
import repair_validation as validation
import store
from workflow import canonical,digest

ARCHIVE_SHA='34a8a969c03268a73dbcdb89c699d7712f8fb9f3c0559a38c49347ac099354fa'
BASE='jumpserve-back-end/.test-artifacts/native-repair-handoff/20261031T230926512822Z/'
CHECKPOINT_SHA='7c21f6465c0554e868c1d2d37dd2229fe4aa05bf05d7c201897687e5ee97e448'
BASELINE_SHA='bea7bf675043463cec9dbf2d576be739e9b9923659358092a8b1dbfe980429ff'
SOURCE_SHA='b8d4042c9431e9b9d97015add592662b7edf442fd1349c2d54c08289c4067210'
TESTS_SHA='b7abb560ac702a8d31a8f3ce46808cea554b24954bbc54d7d134a6c79e4ac648'


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--archive',required=True,type=Path);args=parser.parse_args()
    archive_raw=args.archive.read_bytes();assert digest(archive_raw)==ARCHIVE_SHA
    destination=Path(__file__).resolve().parents[2]/'.test-artifacts/repair-completion-v1'/dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    destination.mkdir(parents=True)
    artifacts={}
    def retain(job,name,raw,*unused):
        artifacts[name]=raw;path=destination/('original-'+name);path.write_bytes(raw)
        return dict(artifact_id=str(uuid.uuid5(uuid.UUID(job['id']),name)),sha256=digest(raw),version=builder.VERSION)
    with zipfile.ZipFile(io.BytesIO(archive_raw)) as archive:
        old_raw=archive.read(BASE+'report.json');old=json.loads(old_raw)
        controller_raw=archive.read(BASE+'controller-events.json');logical=json.loads(controller_raw)[0]['request']
        assert old['status']=='failed' and old['actual_provider_calls']==12 and old['receipt']['build_state']=='checkpoint'
        checkpoint=archive.read(BASE+'original-checkpoint.zip');baseline=archive.read(BASE+'original-live-failed-workspace.zip')
        assert digest(checkpoint)==CHECKPOINT_SHA and digest(baseline)==BASELINE_SHA
        # Every original call is complete; no uncertain provider call is retried.
        for index in range(1,13):
            receipt=json.loads(archive.read(BASE+'slice/call-'+str(index)+'-completion.json'))
            response=archive.read(BASE+'slice/'+receipt['response_filename'])
            assert receipt['status']=='complete' and receipt['http_status']==200 and digest(response)==receipt['response_sha256']
            assert digest(archive.read(BASE+'actual-request-'+str(index)+'.json'))==receipt['request_sha256']
            runtime.wire.read_terminal_stream(io.BytesIO(response),io.BytesIO(),__import__('time').monotonic()+10,1048576)
    with tempfile.TemporaryDirectory(prefix='jumpserve-completion-control-') as tmp:
        root=Path(tmp).resolve();builder.restore(checkpoint,root)
        assert not (root/'candidate.json').exists() and not (root/'repair-test-plan.json').exists() and not (root/'project/repair-test-plan.json').exists()
        source=root/'project/experiment.py';checks=root/'project/repair_test.py'
        assert digest(source.read_bytes())==SOURCE_SHA and digest(checks.read_bytes())==TESTS_SHA
        ctx=json.loads((root/'research-context.json').read_bytes());ctx_before=canonical(ctx)
        assert ctx['native_repair_handoff']['baseline_checkpoint']['sha256']==BASELINE_SHA
        history=builder.load_history(root)
        message,delivery=builder.bootstrap(dict(logical,builder=dict(logical['builder'],context=ctx)),root,root,history)
        assert all(system['text'] in message for system in logical['system']) and len(message.encode())<=builder.BOOTSTRAP_BYTES
        assert canonical(ctx)==ctx_before and delivery['repair_metadata']['created_by_server']
        selected=(root/'software-candidate-template.json').read_bytes()
        completed=subprocess.run([sys.executable,str(root/'finish-repair.py'),'software-candidate-template.json'],capture_output=True,check=True)
        staging=json.loads(completed.stdout);assert (root/'candidate.json').read_bytes()==selected
        job=dict(id=str(uuid.uuid4()),study_id=str(uuid.uuid4()),lease_token=str(uuid.uuid4()))
        actual_popen=subprocess.Popen
        def fixed_local_runner(command,**kwargs):
            assert command[-3:]==['-I','-c',validation.RUNNER]
            mount=command[command.index('--mount')+1];cwd=Path(mount.split('src=',1)[1].split(',dst=',1)[0])
            assert digest((cwd/'experiment.py').read_bytes())==SOURCE_SHA and digest((cwd/'repair_test.py').read_bytes())==TESTS_SHA
            return actual_popen([sys.executable,'-I','-c',validation.RUNNER],cwd=cwd,**kwargs)
        with patch.object(store,'artifact_bytes',return_value=baseline),patch.object(validation.subprocess,'Popen',side_effect=fixed_local_runner),patch.dict(os.environ,OPENCLAW_BUILDER_IMAGE='sha256:'+'f'*64):
            validation.validate(job,root,ctx,retain)
        with patch.object(builder,'retain',side_effect=retain),patch.object(store,'rpc',return_value={}):compiled=builder.freeze_candidate(job,root)
        report=json.loads(artifacts['repair-validation.json']);assert report['passed'] and report['source_change']['changed']
        assert report['test_result']['tests_run']==2 and report['test_result']['experiment_functions_executed']==['convert']
        assert generated_package.members(artifacts['project.zip'])['repair_test.py']==checks.read_bytes()
        assert digest(source.read_bytes())==SOURCE_SHA and digest(checks.read_bytes())==TESTS_SHA
        assert {c['details']['test_config']['private_inputs']['project.zip']['sha256'] for c in compiled['protocol']['configurations']}=={report['project_sha256']}
    result=dict(version='repair-completion-retained-control-v1',status='passed',archive_sha256=ARCHIVE_SHA,original_report_sha256=digest(old_raw),
        original_trial_status='failed',original_trial_provider_calls=12,new_provider_calls=0,original_checkpoint_sha256=CHECKPOINT_SHA,baseline_checkpoint_sha256=BASELINE_SHA,
        original_controller_events_sha256=digest(controller_raw),full_original_trusted_instructions_preserved=True,
        original_bytes_unchanged=digest(args.archive.read_bytes())==ARCHIVE_SHA,server_metadata=delivery['repair_metadata'],context_delivery=delivery,staging=staging,
        candidate_selection='Explicit scripted selection of original prospective fixture; not a model action.',
        validation=report,actual_docker_execution=False,actual_openclaw_execution=False,persistence='fixture',production_writes=0,
        live_unattended_completion_proven=False,scientific_completion=False,
        sources={name:digest((Path(builder.__file__).parent/name).read_bytes()) for name in ('experiment_builder.py','repair_validation.py','autonomous_repair.py',
            'policies/repair-completion-validation-v1.json','tests/test_repair_completion.py','tests/check_repair_completion_retained.py','tests/check_native_repair_handoff_linux.py')},
        artifacts=[dict(path=name,sha256=digest(raw),bytes=len(raw)) for name,raw in artifacts.items()],
        reviewer=dict(identity='Codex implementing AI',independent=False,human=False,held_out=False))
    assert result['original_bytes_unchanged'];(destination/'report.json').write_bytes(canonical(result))
    print(json.dumps(dict(report=str(destination/'report.json'),status='passed',new_provider_calls=0,live_unattended_completion_proven=False)))


if __name__=='__main__':main()
