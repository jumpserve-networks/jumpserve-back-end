"""Invalid arithmetic inputs and external bundles preserve evidence without partial promotion."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import importer
import runner
import workflow
import test_importer as imports
import test_preparation as preparation
import test_workflow as arithmetic


class ArithmeticContracts(unittest.TestCase):
    def test_invalid_protocol_observation_shapes_and_budgets_produce_failed_empty_results(self):
        for defect in ['frozen-hash','input-shape','observation-budget','unknown-config','unknown-status','missing-reason','unrecorded-published','missing-location','missing-tolerance']:
            with self.subTest(defect=defect):
                raw,protocol,campaign=arithmetic.fixture();data=json.loads(raw)
                if defect=='input-shape':data=[]
                elif defect=='observation-budget':data['observed']*=3;protocol['document']['resource_limits']['max_observations']=1
                elif defect=='unknown-config':data['observed'][0]['configuration_identity']='undeclared'
                elif defect=='unknown-status':data['observed'][0]['status']='unrecognized'
                elif defect=='missing-reason':data['observed'][0].update(status='missing',value=None,reason=None)
                elif defect=='unrecorded-published':data['published'][0].update(status='missing',value=None,reason='Original cell absent')
                elif defect=='missing-location':data['published'][0]['location']=''
                elif defect=='missing-tolerance':protocol['document']['metrics'][0].pop('tolerance_absolute')
                raw=workflow.canonical(data);protocol['document']['input_versions'][0]['sha256']=workflow.digest(raw);protocol['sha256']='a'*64 if defect=='frozen-hash' else workflow.digest(workflow.canonical(protocol['document']))
                result=runner.execute(raw,protocol,campaign,str(uuid.uuid4()))
                self.assertEqual(result['run']['status'],'failed');self.assertTrue(result['run']['reason']);self.assertEqual(result['run']['input_sha256'],workflow.digest(raw))
                for kind in ['measurements','published_values','summaries','comparisons']:self.assertEqual(result[kind],[])
        with self.assertRaisesRegex(ValueError,'object'):runner.key([])


class ImportContracts(unittest.TestCase):
    def test_external_metadata_schema_and_duplicate_record_identities_fail_before_import(self):
        with tempfile.TemporaryDirectory() as directory:
            for defect in ['version','records','artifacts','artifact-fields','duplicate-record']:
                with self.subTest(defect=defect):
                    path,data=imports.ExternalEvidence().fixture(directory)
                    if defect=='version':data['version']=2
                    elif defect=='records':data['records']=[]
                    elif defect=='artifacts':data['artifacts']=[]
                    elif defect=='artifact-fields':data['artifacts'][0]['unexpected']='not allowed'
                    else:data['records']*=2
                    path.write_bytes(workflow.canonical(data))
                    with self.assertRaises(ValueError):importer.prepare(path,str(uuid.uuid4()))

    def test_originals_and_aggregate_bundle_sizes_are_bounded_before_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            path,data=imports.ExternalEvidence().fixture(directory)
            with path.open('wb') as stream:stream.truncate(10000001)
            with self.assertRaisesRegex(ValueError,'metadata exceeds'):importer.prepare(path,str(uuid.uuid4()))
            path,data=imports.ExternalEvidence().fixture(directory);original=Path(directory)/'input.bin'
            with original.open('wb') as stream:stream.truncate(10000001)
            with self.assertRaisesRegex(ValueError,'original file exceeds'):importer.prepare(path,str(uuid.uuid4()))
            data['artifacts']=[];raw=b'x'*10000000
            for index in range(3):
                name='input-'+str(index)+'.bin';(Path(directory)/name).write_bytes(raw);data['artifacts'].append(dict(path=name,sha256=workflow.digest(raw),media_type='application/octet-stream',version='original',transformation='none'))
            path.write_bytes(workflow.canonical(data))
            with self.assertRaisesRegex(ValueError,'aggregate byte budget'):importer.prepare(path,str(uuid.uuid4()))

    def test_source_only_import_retains_metadata_without_inventing_an_executed_run(self):
        with tempfile.TemporaryDirectory() as directory:
            path,data=imports.ExternalEvidence().fixture(directory);source=copy.deepcopy(preparation.small_plan()['records'][0]);data['records']=[source];path.write_bytes(workflow.canonical(data))
            prepared,raw,_=importer.prepare(path,str(uuid.uuid4()));self.assertEqual(prepared['records'],[source]);self.assertEqual(raw,path.read_bytes())


if __name__=='__main__':unittest.main()
