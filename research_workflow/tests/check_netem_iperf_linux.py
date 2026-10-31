"""Fixed local Docker integration check; no AWS, database or model credentials.

Uses the lab's already installed test image. Only the fixed runner and a fresh
artifact directory are mounted. The Linux guest has no external network. This
is a software smoke check, not a reproduction of the paper's 30-second study.
"""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import netem_iperf

ROOT=Path(__file__).resolve().parents[2]
parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--full-grid',action='store_true');parser.add_argument('--amd64',action='store_true');args=parser.parse_args()
STAMP=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
OUT=ROOT/'.test-artifacts/executable-methods/linux'/STAMP
OUT.mkdir(parents=True,exist_ok=False)
IMAGE='jumpserve-netem-iperf-test:local'
if args.amd64:IMAGE=json.loads((ROOT/'.test-artifacts/builder-image/selected-linux-amd64.json').read_bytes())['image']
config=dict(cca='cubic',loss_levels_pct=[0,1,2,5,10] if args.full_grid else [0,5],rate_mbit=100,one_way_delay_ms=20,duration_seconds=30 if args.full_grid else 3,queue_packets=1000,mtu=1500,socket_buffer_bytes=131072,seed=27301,loss_seed=None,ping_count=3)
replications=3 if args.full_grid else 1
spec=dict(runner=netem_iperf.VERSION,config=config,replication=0,implementation_sha256=hashlib.sha256(Path(netem_iperf.__file__).read_bytes()).hexdigest())
(OUT/'spec.json').write_text(json.dumps(spec))
protocol=dict(version='netem-iperf-linux-smoke-v2',amendment='Explicit loss_seed=null for older tc versions; condition order remains seeded. Earlier failures retained.',stage='development',questions=['Does the fixed runner execute in isolated Linux and emit actual receiver byte/time/rate, idle RTT and retransmission observations?'],
 comparison='All 10 observations recorded; goodput equals 8*receiver bytes/actual seconds/1e6; at least one loss-free idle RTT within 35..60 ms. No directional goodput prediction or paper-verdict criterion.',
 units=netem_iperf.METRICS,config=config,replications=replications,dependence='Conditions and sequential invocations share one Docker host, not independent hosts.',
 exclusions='No data excluded or trial retried. Failures retained. New invocation gets a new directory.',resources=dict(timeout_seconds=120,cpus=2,memory_mb=512,network='none',new_model_calls=0,new_cloud_instances=0),
 missing='Missing/invalid/ambiguous values stay unavailable, never zero.',intervals='No inferential intervals supported by this smoke design.',
 reviewer=dict(type='AI',identity='Primary implementing Codex assistant',independent=False),measured_charges_usd=None)
(OUT/'protocol.json').write_text(json.dumps(protocol,indent=2))
report=dict(protocol_sha256=hashlib.sha256((OUT/'protocol.json').read_bytes()).hexdigest(),passed=False,reason=None)
try:
    inspect=subprocess.run(['docker','image','inspect',IMAGE],capture_output=True,timeout=20)
    (OUT/'image-inspect.json').write_bytes(inspect.stdout)
    if inspect.returncode: raise RuntimeError('Required preinstalled Docker test image unavailable.')
    image_id=json.loads(inspect.stdout)[0]['Id'];report['image_id']=image_id
    report['platform']=json.loads(inspect.stdout)[0]['Os']+'/'+json.loads(inspect.stdout)[0]['Architecture'];report['host_platform']=__import__('platform').machine();report['emulated']=report['platform']=='linux/amd64' and report['host_platform'] not in ('x86_64','AMD64')
    all_rows=[];report['trials']=[]
    for replication in range(replications):
     trial=OUT/('trial-'+str(replication));trial.mkdir();trial_spec=dict(spec,replication=replication);(trial/'spec.json').write_text(json.dumps(trial_spec))
     command=['docker','run','--rm','--privileged','--network','none','--cpus','2','--memory','512m','--pids-limit','256',
             '--mount','type=bind,src='+str(Path(netem_iperf.__file__).resolve())+',dst=/runner.py,readonly',
             '--mount','type=bind,src='+str(trial.resolve())+',dst=/evidence',
             '-e','JUMPSERVE_SPEC=/evidence/spec.json','-e','JUMPSERVE_OUTPUT=/evidence/output.json',image_id,'python3','/runner.py']
     result=subprocess.run(command,capture_output=True,timeout=240 if args.full_grid else 120)
     (trial/'stdout.bin').write_bytes(result.stdout);(trial/'stderr.bin').write_bytes(result.stderr);report['trials'].append(dict(replication=replication,exit_code=result.returncode))
     if result.returncode: raise RuntimeError('Linux runner exited unsuccessfully; inspect retained stderr.')
     output=json.loads((trial/'output.json').read_bytes());rows=output['observations'];all_rows.extend(rows)
     if len(rows)!=len(config['loss_levels_pct'])*5: raise RuntimeError('Declared observation coverage is incomplete; inspect original command results.')
     for loss in config['loss_levels_pct']:
        r={x['metric']:x['value'] for x in rows if x['metadata']['loss_pct']==loss}
        if any(r.get(k) is None for k in ('throughput_mbit_s','received_bytes','duration_seconds')):raise RuntimeError('Receiver conservation check lacks a required observation; original missing values retained.')
        if abs(r['throughput_mbit_s']-8*r['received_bytes']/r['duration_seconds']/1e6)>1e-8: raise RuntimeError('Receiver conservation check failed.')
     if not any(r['metric']=='idle_rtt_ms' and r['metadata']['loss_pct']==0 and r['value'] is not None and 35<=r['value']<=60 for r in rows): raise RuntimeError('Configured unloaded RTT not observed within smoke-check range.')
     print(json.dumps(dict(replication=replication,status='recorded',observations=len(rows))),flush=True)
    report.update(passed=True,observations=len(all_rows),units=netem_iperf.METRICS,scope='Actual Linux namespace TCP measurements; exposed development qualification, not production paper evidence.',statuses={s:sum(r['status']==s for r in all_rows) for s in ('recorded','missing','invalid','ambiguous','excluded')})
except Exception as error:report['reason']=str(error)
finally:
    report['artifacts']=[dict(name=str(p.relative_to(OUT)),bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(OUT.rglob('*')) if p.is_file()]
    (OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(dict(report=str(OUT/'report.json'),passed=report['passed'],reason=report['reason'])))
    if report['passed'] and args.full_grid:(OUT.parent/'full-grid-selected.json').write_text(json.dumps(dict(directory=str(OUT),report_sha256=hashlib.sha256((OUT/'report.json').read_bytes()).hexdigest()))+'\n')
raise SystemExit(0 if report['passed'] else 1)
