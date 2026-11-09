"""Failure capture and bounded development checks, without Docker execution."""
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch
import uuid
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import repair_validation as validation,generated_package,source_project,store
import experiment_builder
from test_native_repair_handoff import context as repair_context
from workflow import canonical,digest


class RepairValidationBoundaries(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.root=Path(temporary.name);self.context=repair_context();self.context['native_repair_handoff']=experiment_builder.repair_handoff(self.context);self.job={'id':str(uuid.uuid4()),'study_id':str(uuid.uuid4())};self.retained={}

    def retain(self,job,name,raw):
        self.retained[name]=raw;return {'artifact_id':name,'sha256':digest(raw)}

    def test_failed_diagnostic_replace_removes_only_the_temporary_copy_and_retains_existing_originals(self):
        original=self.root/'repair-check-stdout.bin';original.write_bytes(b'Original prior diagnostic')
        with patch.object(validation.os,'replace',side_effect=OSError('Synthetic atomic publication failure')),self.assertRaisesRegex(OSError,'publication'):validation.deliver_current(self.root,{},b'New diagnostic',b'',{})
        self.assertEqual(original.read_bytes(),b'Original prior diagnostic');self.assertEqual(list(self.root.glob('.repair-diagnostic-*')),[])

    def test_concurrent_plan_publication_revalidates_the_original_rather_than_overwriting_it(self):
        # Model and controller may publish the same fixed metadata concurrently.
        context=self.context
        def publish(source,destination):Path(destination).write_bytes(Path(source).read_bytes());raise FileExistsError('Original concurrent plan won')
        with patch.object(validation.os,'link',side_effect=publish):result=validation.prepare_test_plan(self.root,context)
        self.assertEqual(result['status'],'valid');self.assertFalse(result['created_by_server']);self.assertEqual(list(self.root.glob('.repair-plan-*')),[])

    def test_ambiguous_baseline_sources_cannot_be_certified_as_a_repair(self):
        raw=io.BytesIO()
        with zipfile.ZipFile(raw,'w') as archive:
            info=zipfile.ZipInfo('project/experiment.py');info.external_attr=(stat.S_IFLNK|0o777)<<16;archive.writestr(info,b'Original linked source')
        original=raw.getvalue();context=dict(self.context,native_repair_handoff=dict(self.context['native_repair_handoff'],baseline_checkpoint={'artifact_id':'original','sha256':digest(original)}))
        with patch.object(store,'artifact_bytes',return_value=original),self.assertRaisesRegex(ValueError,'ambiguous or invalid'):validation.source_change(self.job,context,b'Unchanged candidate parameter')

    def validate(self,mode):
        process=Mock();process.returncode=0;process.poll.return_value=None if mode=='deadline' else 0
        def launch(command,**kwargs):
            if mode=='invalid-result':kwargs['stdout'].write(b'Original non-JSON diagnostic\n')
            return process
        image='' if mode=='unpinned' else 'sha256:'+'f'*64
        with patch.object(validation,'source_change',return_value={'changed':True}),patch.object(validation,'test_plan',return_value=[]),patch.dict(os.environ,OPENCLAW_BUILDER_IMAGE=image),patch.object(validation.subprocess,'Popen',side_effect=launch),patch.object(validation.subprocess,'run') as remove,patch.object(validation.time,'monotonic',side_effect=[0,26]),patch.object(source_project,'freeze',return_value=b'Original sealed source'):
            if mode=='unpinned':
                with self.assertRaisesRegex(ValueError,'Pinned repair validation image'):validation._validate(self.job,self.root,self.context,self.retain,self.root,b'Original sealed source')
            else:
                with self.assertRaises(validation.RepairCheckFailure):validation._validate(self.job,self.root,self.context,self.retain,self.root,b'Original sealed source')
                report=json.loads(self.retained['repair-validation.json']);self.assertFalse(report['passed']);self.assertEqual(report['stop_reason'],'time-or-output-limit' if mode=='deadline' else None)
                if mode=='deadline':process.kill.assert_called_once();process.wait.assert_called_once_with(timeout=5);self.assertEqual(remove.call_args.args[0][:3],['docker','rm','-f'])
                else:self.assertEqual(self.retained['repair-check-stdout.bin'],b'Original non-JSON diagnostic\n')

    def test_unpinned_validation_image_is_refused_before_process_execution(self):self.validate('unpinned')
    def test_expired_development_check_is_killed_and_retained_as_a_failed_original(self):self.validate('deadline')
    def test_invalid_final_result_is_retained_and_never_reinterpreted_as_a_pass(self):self.validate('invalid-result')


if __name__=='__main__':unittest.main()
