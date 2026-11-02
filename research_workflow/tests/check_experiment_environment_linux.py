"""Fixed operational qualification on a disposable Linux host, never paper data.

Installs fixed dependencies and raises host socket maxima. Run only on a fresh
dedicated qualification EC2 host. Original and corrected outputs remain on disk;
there are no AWS, database or model calls and no automatic trial retries.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import netem_iperf,package_runtime


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dedicated-experiment-host',action='store_true',required=True)
    parser.parse_args()
    if platform.system()!='Linux' or os.geteuid()!=0:raise ValueError('Dedicated root Linux qualification host required.')
    root=Path('/var/lib/jumpserve-environment-qualification')/str(uuid.uuid4());root.mkdir(parents=True,mode=0o700)
    deadline=time.time()+900
    report=dict(version='experiment-environment-linux-qualification-v1',passed=False,
        started_at=dt.datetime.now(dt.timezone.utc).isoformat(),kernel=platform.release(),cases=[],
        scope='Exposed operational qualification on one host; no paper finding, independent replications or deployment proof.')
    # A fresh host's exact tool/socket defaults precede any modification.
    baseline={}
    for tool,args in [('sysctl',['-n','net.core.rmem_max','net.core.wmem_max']),('tc',['qdisc','add','dev','lo','root','netem','help'])]:
        result=subprocess.run([tool,*args],capture_output=True,timeout=10) if shutil.which(tool) else None
        baseline[tool]=dict(available=result is not None,returncode=result.returncode if result else None,
            stdout=result.stdout.decode(errors='replace') if result else None,stderr=result.stderr.decode(errors='replace') if result else None)
    (root/'baseline.json').write_bytes(canonical(baseline))
    previous=os.environ.get('JUMPSERVE_DEDICATED_EXPERIMENT')
    try:
        for name,seed,prepare in [('original-unprepared',None,False),('prepared-unseeded',None,True),('prepared-seeded',104729,True)]:
            directory=root/name;directory.mkdir(mode=0o700)
            config=dict(cca='cubic',loss_levels_pct=[0,5],rate_mbit=100,one_way_delay_ms=20,duration_seconds=3,
                queue_packets=1000,mtu=1500,socket_buffer_bytes=4194304,seed=27301,loss_seed=seed,ping_count=3)
            source=Path(netem_iperf.__file__).read_bytes()
            spec=dict(runner=netem_iperf.VERSION,config=config,replication=0,implementation_sha256=hashlib.sha256(source).hexdigest(),
                limits=dict(wall_seconds=900,max_instances=1,max_output_bytes=3000000,max_network_bytes=10000000000))
            raw=canonical(spec);(directory/'spec.json').write_bytes(raw);(directory/'netem_iperf.py').write_bytes(source)
            if prepare:os.environ['JUMPSERVE_DEDICATED_EXPERIMENT']=hashlib.sha256(raw).hexdigest()
            else:os.environ.pop('JUMPSERVE_DEDICATED_EXPERIMENT',None)
            receipt=package_runtime.run(directory,deadline,allow_container=False)
            output=json.loads((directory/'output.json').read_bytes()) if (directory/'output.json').exists() else {}
            rows=output.get('observations',[])
            complete=receipt['exit_code']==0 and receipt['termination_reason'] is None and len(rows)==10 and all(r['status']=='recorded' and r['value'] is not None for r in rows)
            qualified=(output.get('provenance',{}).get('environment_preparation') or {}).get('qualified') is True
            report['cases'].append(dict(name=name,complete=complete,environment_qualified=qualified,
                statuses={status:sum(r['status']==status for r in rows) for status in ('recorded','missing')},receipt=receipt))
        report['passed']=all(c['complete'] and c['environment_qualified'] for c in report['cases'] if c['name'].startswith('prepared-'))
    finally:
        if previous is None:os.environ.pop('JUMPSERVE_DEDICATED_EXPERIMENT',None)
        else:os.environ['JUMPSERVE_DEDICATED_EXPERIMENT']=previous
        report['artifacts']=[dict(path=str(p.relative_to(root)),bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(root.rglob('*')) if p.is_file()]
        (root/'report.json').write_bytes(canonical(report))
        print(json.dumps(dict(passed=report['passed'],report=str(root/'report.json'))))
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
