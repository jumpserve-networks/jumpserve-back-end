"""Fixed local Docker integration check; no AWS, database or model credentials.

Uses the lab's already installed test image. Only the fixed runner and a fresh
artifact directory are mounted. The Linux guest has no external network. This
is a software smoke check, not a reproduction of the paper's 30-second study.
"""
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import netem_iperf

ROOT=Path(__file__).resolve().parents[2]
STAMP=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
OUT=ROOT/'.test-artifacts/executable-methods/linux'/STAMP
OUT.mkdir(parents=True,exist_ok=False)
IMAGE='jumpserve-netem-iperf-test:local'
config=dict(cca='cubic',loss_levels_pct=[0,5],rate_mbit=100,one_way_delay_ms=20,duration_seconds=3,queue_packets=1000,mtu=1500,socket_buffer_bytes=131072,seed=27301,loss_seed=None,ping_count=3)
spec=dict(runner=netem_iperf.VERSION,config=config,replication=0,implementation_sha256=hashlib.sha256(Path(netem_iperf.__file__).read_bytes()).hexdigest())
(OUT/'spec.json').write_text(json.dumps(spec))
protocol=dict(version='netem-iperf-linux-smoke-v2',amendment='Explicit loss_seed=null for older tc versions; condition order remains seeded. Earlier failures retained.',stage='development',questions=['Does the fixed runner execute in isolated Linux and emit actual receiver byte/time/rate, idle RTT and retransmission observations?'],
 comparison='All 10 observations recorded; goodput equals 8*receiver bytes/actual seconds/1e6; at least one loss-free idle RTT within 35..60 ms. No directional goodput prediction or paper-verdict criterion.',
 units=netem_iperf.METRICS,config=config,replications=1,dependence='Two conditions share one Docker Linux guest, not independent hosts.',
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
    command=['docker','run','--rm','--privileged','--network','none','--cpus','2','--memory','512m','--pids-limit','256',
             '--mount','type=bind,src='+str(Path(netem_iperf.__file__).resolve())+',dst=/runner.py,readonly',
             '--mount','type=bind,src='+str(OUT.resolve())+',dst=/evidence',
             '-e','JUMPSERVE_SPEC=/evidence/spec.json','-e','JUMPSERVE_OUTPUT=/evidence/output.json',image_id,'python3','/runner.py']
    result=subprocess.run(command,capture_output=True,timeout=120)
    (OUT/'stdout.bin').write_bytes(result.stdout);(OUT/'stderr.bin').write_bytes(result.stderr);report['exit_code']=result.returncode
    if result.returncode: raise RuntimeError('Linux runner exited unsuccessfully; inspect retained stderr.')
    output=json.loads((OUT/'output.json').read_bytes());rows=output['observations']
    if len(rows)!=10 or any(r['status']!='recorded' for r in rows): raise RuntimeError('Declared observation coverage is incomplete; inspect original command results.')
    for loss in (0,5):
        r={x['metric']:x['value'] for x in rows if x['metadata']['loss_pct']==loss}
        if abs(r['throughput_mbit_s']-8*r['received_bytes']/r['duration_seconds']/1e6)>1e-8: raise RuntimeError('Receiver conservation check failed.')
    if not any(r['metric']=='idle_rtt_ms' and r['metadata']['loss_pct']==0 and 35<=r['value']<=60 for r in rows): raise RuntimeError('Configured unloaded RTT not observed within smoke-check range.')
    report.update(passed=True,observations=len(rows),units=netem_iperf.METRICS,scope='Actual Linux namespace TCP measurements; software smoke only, not paper reproduction.')
except Exception as error:report['reason']=str(error)
finally:
    report['artifacts']=[dict(name=p.name,bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(OUT.iterdir()) if p.is_file()]
    (OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(dict(report=str(OUT/'report.json'),passed=report['passed'],reason=report['reason'])))
raise SystemExit(0 if report['passed'] else 1)
