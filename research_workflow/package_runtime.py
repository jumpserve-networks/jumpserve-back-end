"""Experiment-machine runtime. Never import this to execute source on an API host.

Inputs: spec.json and package.py (for Python packages). Outputs: original stdout,
stderr and output.json plus a runtime receipt. Packages must implement the explicit
network-observations-v1 contract; execution success is not scientific validation.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time

def run(directory, deadline, allow_container=True):
    root=Path(directory).resolve();spec=json.loads((root/'spec.json').read_text());builtin=spec.get('runner')=='netem-iperf-v1';package=spec['config'].get('package',{}) if builtin else spec['config']['package']
    if builtin: package=dict(kind='builtin-netem-iperf',version='netem-iperf-v1')
    started=dt.datetime.now(dt.timezone.utc).isoformat();tick=time.monotonic();reason=None;process=None
    receipt=dict(version='network-package-runtime-v1',started_at=started,execution_kind=package['kind'],package_version=package['version'],exit_code=None,
        measured_charges_usd=None,process_wall_seconds=None,termination_reason=None,container_image=package.get('image'),network_bytes=None)
    # Run packages without parent credentials, report capabilities or cloud SDK env.
    env={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8','JUMPSERVE_SPEC':str(root/'spec.json'),'JUMPSERVE_OUTPUT':str(root/'output.json'),'JUMPSERVE_INPUTS':str(root/'inputs')}
    arguments=spec['config'].get('arguments',[])
    if builtin:
        command=['python3',str(root/'netem_iperf.py')]
    elif package['kind']=='python-script':
        raw=(root/'package.py').read_bytes()
        if hashlib.sha256(raw).hexdigest()!=package['sha256']: raise ValueError('Package original-byte hash differs on the experiment machine.')
        command=['python3',str(root/'package.py'),*arguments]
    elif package['kind']=='container' and allow_container:
        name='jumpserve-'+root.name[:50]
        command=['docker','run','--rm','--name',name,'--network','host','--memory','1g','--cpus','1','--pids-limit','256','--cap-add','NET_ADMIN',
            '-v',str(root)+':/experiment','-e','JUMPSERVE_SPEC=/experiment/spec.json','-e','JUMPSERVE_OUTPUT=/experiment/output.json','-e','JUMPSERVE_INPUTS=/experiment/inputs',
            *(['--cap-add','SYS_ADMIN'] if spec['config']['mode']=='emulated' else []),package['image'],*arguments]
    else: raise ValueError('Container execution is unavailable in this runtime.')
    initial=network_bytes()
    try:
        with (root/'stdout.bin').open('xb') as out,(root/'stderr.bin').open('xb') as err:
            process=subprocess.Popen(command,cwd=root,env=env,stdout=out,stderr=err,start_new_session=True)
            while process.poll() is None:
                consumed=network_bytes();elapsed=time.time()>=deadline
                files=sum(p.stat().st_size for p in root.iterdir() if p.is_file() and p.name not in ('spec.json','package.py'))
                output_exceeded=files>max(0,spec['limits']['max_output_bytes']-1024)
                if elapsed or output_exceeded or initial is not None and consumed is not None and consumed-initial>spec['limits']['max_network_bytes']:
                    reason='Wall deadline exceeded.' if elapsed else 'Output byte budget exceeded.' if output_exceeded else 'Network byte budget exceeded.'
                    os.killpg(process.pid,signal.SIGTERM)
                    try: process.wait(timeout=3)
                    except subprocess.TimeoutExpired: os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=3)
                    break
                time.sleep(.05)
            receipt['exit_code']=process.returncode
    finally:
        if package['kind']=='container' and allow_container:
            subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15,check=False)
        final=network_bytes();receipt.update(ended_at=dt.datetime.now(dt.timezone.utc).isoformat(),process_wall_seconds=max(0,time.monotonic()-tick),termination_reason=reason,
            network_bytes=final-initial if initial is not None and final is not None and final>=initial else None,
            interface_counter_reset=initial is not None and final is not None and final<initial,
            network_counter_scope='Non-loopback receive/transmit counters in the runtime network namespace. Child namespace counters remain in raw experiment diagnostics and are not included here. Forwarding can be counted multiple times. Unavailable/reset counters are missing, not zero; this is not billed traffic.',
            enforcement_scope='50-ms sampled output and network counters, with possible overshoot; wall timer and independent machine shutdown. Python is bounded by host capacity; container adds 1 CPU, 1 GiB and 256 PID limits.')
        (root/'runtime-receipt.json').write_text(json.dumps(receipt,sort_keys=True,allow_nan=False)+'\n')
    return receipt

def network_bytes():
    path=Path('/proc/net/dev')
    if not path.exists(): return None
    total=0
    for line in path.read_text().splitlines()[2:]:
        name,values=line.split(':',1);fields=values.split()
        if name.strip()!='lo':total+=int(fields[0])+int(fields[8])
    return total

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',required=True);parser.add_argument('--deadline',required=True,type=float)
    args=parser.parse_args();result=run(args.directory,args.deadline)
    raise SystemExit(0 if result['exit_code']==0 and not result['termination_reason'] else 1)
