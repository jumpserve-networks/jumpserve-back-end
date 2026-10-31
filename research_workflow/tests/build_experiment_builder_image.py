"""Build the fixed Ubuntu builder fixture for this worker UID/GID; no model calls."""
import argparse,datetime as dt,json,os,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
BASE='ubuntu@sha256:e0f80c829509ef4df9e76bd8944eb7b8ec25cbb379b112a7f9448b2c77a77c9b'
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--platform',choices=['linux/amd64','linux/arm64']);args=parser.parse_args()
    out=ROOT/'.test-artifacts/builder-image'/dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');out.mkdir(parents=True)
    assert os.getuid()>0 and os.getgid()>0,'Run the local fixture as the nonroot worker user.'
    tag='jumpserve-builder-test:'+args.platform.replace('/','-') if args.platform else 'jumpserve-builder-test:current'
    command=['docker','build',*(['--platform',args.platform] if args.platform else []),'--build-arg','BASE_IMAGE='+BASE,'--build-arg','WORKSPACE_UID='+str(os.getuid()),'--build-arg','WORKSPACE_GID='+str(os.getgid()),'-t',tag,'-f',str(ROOT/'research_workflow/experiment-builder.Dockerfile'),str(ROOT/'research_workflow')]
    with (out/'build.log').open('wb') as sink:completed=subprocess.run(command,stdout=sink,stderr=subprocess.STDOUT,timeout=600)
    if completed.returncode:raise SystemExit('Builder fixture build failed; retained log: '+str(out/'build.log'))
    image=json.loads(subprocess.check_output(['docker','image','inspect',tag]))[0]
    if args.platform and image['Os']+'/'+image['Architecture']!=args.platform:raise ValueError('Built platform differs from the requested qualification architecture.')
    report=dict(status='passed',image=image['Id'],base=BASE,uid=os.getuid(),gid=os.getgid(),platform=image['Os']+'/'+image['Architecture'],live_provider_calls=0,measured_charges_usd=None,command=command,dockerfile_sha256=__import__('hashlib').sha256((ROOT/'research_workflow/experiment-builder.Dockerfile').read_bytes()).hexdigest())
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    name='selected-'+args.platform.replace('/','-')+'.json' if args.platform else 'selected.json'
    (ROOT/'.test-artifacts/builder-image'/name).write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(directory=str(out),**report)))
if __name__=='__main__':main()
