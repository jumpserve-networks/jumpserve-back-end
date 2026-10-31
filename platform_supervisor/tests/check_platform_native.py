"""Fixed local Docker/OpenClaw development control; optional bounded live model.

No production Supabase, GitHub or AWS writes. The ledger is a local fixture;
the same SQL admission algorithms are tested separately in isolated PostgreSQL.
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'research_workflow/tests'))
from platform_supervisor import native
from platform_supervisor.common import canonical,digest,identity,save
from check_experiment_builder_linux import Response,sse
from prepare_builder_vendor import prepare

class LocalStore:
    def __init__(self,directory):
        self.spool=directory/'spool';self.task=dict(id=str(uuid.uuid4()),lease_token=str(uuid.uuid4()));self.artifacts=[];self.runs=[]
    def rows(self,table):return self.runs if table=='runs' else []
    def artifact(self,name,raw,provenance=None):
        ident=identity(self.task['id'],digest(raw));save(self.spool/self.task['id']/'artifacts'/digest(raw),raw)
        item=dict(id=ident,name=name,sha256=digest(raw),byte_count=len(raw));self.artifacts.append(item);return item
    def rpc(self,operation,payload=None):
        if operation=='begin-run':
            if len(self.runs)>=6:return dict(denied='budget')
            run=dict(payload,status='running',task_id=self.task['id']);self.runs.append(run);return run
        if operation=='finish-run':
            run=next(r for r in self.runs if r['id']==payload['id']);run.update(payload);return run
        if operation=='reconcile-run':return dict(reconciled=True)
        raise AssertionError(operation)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--live',action='store_true');parser.add_argument('--build-image',action='store_true');args=parser.parse_args()
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');out=ROOT/'.test-artifacts/platform-native'/stamp;out.mkdir(parents=True)
    report=dict(version='platform-native-development-v1',passed=False,live=args.live,maximum_actual_provider_calls=6,maximum_elapsed_seconds=600,maximum_reservation_cents=600,production_writes=0,actual_provider_calls=0,measured_charges_usd=None,independent_review=False,scientific_claim=False)
    save(out/'prospective.json',canonical(report))
    if args.build_image:
        recipe=ROOT/'platform_supervisor/Dockerfile';tag='jumpserve-platform:'+digest(recipe.read_bytes())[:12]
        with (out/'image-build.log').open('wb') as log:subprocess.run(['docker','build','--platform','linux/amd64','-t',tag,'-f',str(recipe),str(recipe.parent)],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=900)
        inspected=json.loads(subprocess.check_output(['docker','image','inspect',tag],timeout=20));image=inspected[0]['Id'];save(ROOT/'.test-artifacts/platform-native/image.json',canonical(dict(image=image,recipe_sha256=digest(recipe.read_bytes()),platform='linux/amd64')))
    else:image=json.loads((ROOT/'.test-artifacts/builder-image/selected-linux-amd64.json').read_bytes())['image']
    report['image']=image
    workspace=out/'workspace';workspace.mkdir();(workspace/'project').mkdir()
    (workspace/'project/calc.py').write_text('def double(value):\n    return value\n')
    test='from calc import double\nassert double(7)==14\nassert double(0)==0\nassert double(None) is None\nprint("three controls passed")\n'
    (workspace/'project/check.py').write_text(test)
    save(workspace/'task.json',canonical(dict(objective='Explicit software fixture. Fix project/calc.py so double multiplies numeric values by two and preserves None. Run python3 project/check.py with the real sandbox exec tool. Preserve check.py unchanged. Write proposal.json version platform-proposal-v1, summary, ready true only after the actual test passes. No research paper, network experiment or production operation is authorized.')))
    steps=[('read',dict(path='/workspace/project/calc.py')),('exec',dict(command='python3 project/check.py',timeoutSeconds=10)),('write',dict(path='/workspace/project/calc.py',content='def double(value):\n    return None if value is None else value * 2\n')),('exec',dict(command='python3 project/check.py',timeoutSeconds=10)),('write',dict(path='/workspace/proposal.json',content=json.dumps(dict(version='platform-proposal-v1',summary='Preserve missing and zero while doubling values.',ready=True))))]
    store=LocalStore(out);index=0
    def transport(method,url,token,body):
        nonlocal index
        index+=1
        if args.live:
            if report['actual_provider_calls']>=6:raise ValueError('Prospective model allowance exhausted before transport.')
            report['actual_provider_calls']+=1;save(out/'live-admissions.json',canonical(dict(actual_provider_calls=report['actual_provider_calls'],reserved_cents=100*report['actual_provider_calls'])))
            return native.runtime.http_request(method,url,token,body)
        return Response(sse(index,*steps[index-1])) if index<=len(steps) else Response(sse(index))
    package=ROOT/'experiments/openclaw_pilot/runtime/node_modules/openclaw';node=package.parent/'node/bin/node'
    corrected=prepare(package,ROOT/'.test-artifacts/builder-vendor/node_modules/openclaw',native.builder.PATCH_PATH)
    try:
        with patch.object(native.runtime,'NODE',node),patch.object(native.runtime,'PACKAGE',package),patch.object(native.runtime,'AUTH',ROOT/'experiments/openclaw_pilot/.subscription-auth'),patch.dict(os.environ,OPENCLAW_BUILDER_PACKAGE=str(corrected)):
            result=native.execute(store,workspace,out/'slice',image,token_source=native.runtime.credential if args.live else lambda:'synthetic-not-a-credential',transport=transport)
        report['result']=result
        assert result['ready'],result
        assert (workspace/'project/check.py').read_text()==test,'Existing acceptance check changed.'
        checked=subprocess.run(['docker','run','--rm','--network','none','--cap-drop','ALL','--mount','type=bind,source='+str(workspace)+',target=/workspace','--workdir','/workspace',image,'python3','project/check.py'],capture_output=True,timeout=30)
        save(out/'verification-stdout.bin',checked.stdout);save(out/'verification-stderr.bin',checked.stderr)
        assert checked.returncode==0 and checked.stdout.strip()==b'three controls passed'
        assert all(r['status']=='complete' for r in store.runs)
        # Reconciliation sees the retained terminal calls and invokes no model.
        with patch.object(native.runtime,'http_request',side_effect=AssertionError('No replay permitted')):native.reconcile(store)
        report.update(passed=True,model_calls=len(store.runs),original_runs=store.runs,artifacts=store.artifacts,existing_test_unchanged=True,container_check_passed=True)
    finally:
        save(out/'report.json',canonical(report));print(json.dumps(dict(passed=report['passed'],actual_provider_calls=report['actual_provider_calls'],report=str(out/'report.json'))))

if __name__=='__main__':main()
