"""Fixed local Docker controls for the repair validator; no models or network.

Uses an already-cached local builder image, NOT the production image or a new
pull/build. Preserves failed controls as intended negative-control outcomes.
"""
import datetime as dt,json,os,pathlib,subprocess,sys,uuid
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import generated_package,repair_validation
from workflow import canonical,digest
ROOT=pathlib.Path(__file__).resolve().parents[2]
IMAGE='sha256:ef9eec8c7c44fa2da5312721295aeea6ee92837a8e7f909c5f70785ecb9207be'

def main():
 assert len(sys.argv)==1,'Fixed validation campaign only.'
 stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');out=ROOT/'.test-artifacts/repair-validation-linux'/stamp;out.mkdir(parents=True)
 report=dict(version='repair-development-validation-v1',started_at=dt.datetime.now(dt.timezone.utc).isoformat(),image=IMAGE,production_image_tested=False,live_model_calls=0,new_cloud_instances=0,checks=[],reviewer=dict(identity='Implementing Codex assistant',type='AI',independent=False,human=False),measured_charges_usd=None)
 inspected=subprocess.check_output(['docker','image','inspect',IMAGE],timeout=10);(out/'image-inspection.json').write_bytes(inspected);assert json.loads(inspected)[0]['Os']=='linux'
 prior=os.environ.get('OPENCLAW_BUILDER_IMAGE');os.environ['OPENCLAW_BUILDER_IMAGE']=IMAGE
 tests='import unittest,experiment\nclass Checks(unittest.TestCase):\n def test_original_failure(self):\n  with self.assertRaises(TypeError):experiment.value(None)\n def test_corrected_behavior(self):\n  self.assertEqual(experiment.value(7),14)\n'
 try:
  for name,code,test,expected in [('corrected','def value(x):\n return x*2\n',tests,True),('noisy-corrected','def value(x):\n print("original command log",end="")\n return x*2\n',tests,True),('changed-code','def value(x):\n return x\n',tests,False),('no-op','def value(x):\n return x*2\n','import unittest\nclass Checks(unittest.TestCase):\n def test_original_failure(self):pass\n def test_corrected_behavior(self):pass\n',False),('output-limit','def value(x):\n print("x"*200000)\n return x*2\n',tests,False),('timeout','import time\ndef value(x):\n time.sleep(60)\n return x*2\n',tests,False)]:
   case=out/name;project=case/'project';project.mkdir(parents=True);job=dict(id=str(uuid.uuid4()));incident=digest(b'Explicit synthetic software incident; not a publication.')
   for path,raw in {'experiment.py':code,'requirements.lock':'','system-packages.lock.json':'[]','build-evidence.json':json.dumps(dict(commands=[],checks=[],limitations=['Software control only.'])),'repair_test.py':test,'repair-test-plan.json':json.dumps(dict(version=repair_validation.VERSION,incident_sha256=incident,scope='software-development-only'))}.items():(project/path).write_text(raw)
   original=generated_package.freeze(project);(case/'original-project.zip').write_bytes(original)
   def retain(job,name,raw):
    (case/name).write_bytes(raw);assert (case/name).read_bytes()==raw
    return dict(artifact_id=str(uuid.uuid5(uuid.UUID(job['id']),name)),sha256=digest(raw),version='experiment-builder-v1')
   error=None
   try:repair_validation.validate(job,case,dict(repair_packet=dict(incident_sha256=incident)),retain);passed=True
   except ValueError as exc:passed=False;error=str(exc)
   assert passed==expected,(name,error)
   retained=json.loads((case/'repair-validation.json').read_bytes());assert retained['passed']==expected
   current=json.loads((case/'repair-validation-current.json').read_bytes());assert current['passed']==expected and current['project_sha256']==digest(original)
   assert current['stdout']['sha256']==digest((case/'repair-check-stdout.bin').read_bytes())
   assert generated_package.freeze(project)==original
   report['checks'].append(dict(name=name,expected_pass=expected,actual_pass=passed,control_passed=True,report_sha256=digest((case/'repair-validation.json').read_bytes()),project_sha256=digest(original)))
  report['passed']=True
 finally:
  if prior is None:os.environ.pop('OPENCLAW_BUILDER_IMAGE',None)
  else:os.environ['OPENCLAW_BUILDER_IMAGE']=prior
  report['ended_at']=dt.datetime.now(dt.timezone.utc).isoformat();(out/'report.json').write_bytes(canonical(report));print(json.dumps(dict(directory=str(out),passed=report.get('passed',False),checks=report['checks'],production_image_tested=False)))
if __name__=='__main__':main()
