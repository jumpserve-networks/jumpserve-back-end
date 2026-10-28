"""Prepare the local versioned networking development image, never publish it.

Fixed Dockerfile/base and validation only; no arbitrary commands, AWS or model
calls. Preserve failed builds and package inventories in timestamped evidence.
Production linux/amd64 qualification remains a separate release requirement.
"""
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workflow import canonical,digest

ROOT=Path(__file__).resolve().parents[2]
BASE='ubuntu@sha256:e0f80c829509ef4df9e76bd8944eb7b8ec25cbb379b112a7f9448b2c77a77c9b'
CHECK='''import json,shutil,subprocess
from pathlib import Path
names=['ip','tc','iperf3','ping','ethtool']
assert all(shutil.which(name) for name in names)
raw=Path('/jumpserve-image-packages.txt').read_text()
assert all(name in raw for name in ['iproute2','iperf3','iputils-ping','ethtool'])
assert subprocess.check_output(['sudo','-n','id','-u']).strip()==b'0'
print(json.dumps(dict(commands={name:shutil.which(name) for name in names},package_inventory=raw,namespace_execution_verified=False)))
'''

def main():
    out=ROOT/'.test-artifacts/builder-image'/dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');out.mkdir(parents=True)
    recipe=ROOT/'research_workflow/experiment-builder.Dockerfile';raw=recipe.read_bytes();(out/'Dockerfile.original').write_bytes(raw)
    report=dict(version='builder-prepared-network-image-v1',status='failed',base=BASE,dockerfile_sha256=digest(raw),live_provider_calls=0,new_cloud_instances=0,measured_charges_usd=None)
    try:
        architecture=subprocess.check_output(['docker','info','--format','{{.Architecture}}'],timeout=20,text=True).strip()
        platform={'aarch64':'linux/arm64','arm64':'linux/arm64','x86_64':'linux/amd64','amd64':'linux/amd64'}[architecture]
        tag='jumpserve-builder:continuation-v1-'+digest(raw)[:12]
        command=['docker','build','--platform',platform,'--build-arg','BASE_IMAGE='+BASE,'--build-arg','WORKSPACE_UID='+str(os.getuid()),'--build-arg','WORKSPACE_GID='+str(os.getgid()),'-t',tag,'-f',str(recipe),str(recipe.parent)]
        report.update(command=command,platform=platform,uid=os.getuid(),gid=os.getgid())
        with (out/'build.log').open('wb') as log:
            result=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=600)
        assert result.returncode==0,'Build failed; original log retained.'
        inspected=subprocess.check_output(['docker','image','inspect',tag],timeout=20);(out/'image-inspect.json').write_bytes(inspected)
        image=json.loads(inspected)[0]['Id'];report['image']=image
        checked=subprocess.run(['docker','run','--rm','--network','none','--user',str(os.getuid())+':'+str(os.getgid()),image,'python3','-I','-c',CHECK],capture_output=True,timeout=30)
        (out/'check-stdout.bin').write_bytes(checked.stdout);(out/'check-stderr.bin').write_bytes(checked.stderr)
        assert checked.returncode==0,'Package/sudo check failed; original output retained.'
        result=json.loads(checked.stdout);inventory=result.pop('package_inventory').encode();(out/'package-inventory.txt').write_bytes(inventory)
        report.update(status='passed',commands=result['commands'],package_inventory_sha256=digest(inventory),namespace_execution_verified=False,scope='Local development image prepared once, with exact resulting image digest and installed package versions. No paper experiment or production image was executed.')
    finally:
        report['artifacts']=[dict(path=p.name,sha256=digest(p.read_bytes()),bytes=p.stat().st_size) for p in sorted(out.iterdir()) if p.is_file()]
        (out/'report.json').write_bytes(canonical(report));print(json.dumps(dict(status=report['status'],report=str(out/'report.json'))))
    if report['status']=='passed':
        (ROOT/'.test-artifacts/builder-image/continuation-v1.json').write_bytes(canonical(dict(report,image=report['image'],report_path=str(out/'report.json'))))
    else:raise SystemExit(1)

if __name__=='__main__':main()
