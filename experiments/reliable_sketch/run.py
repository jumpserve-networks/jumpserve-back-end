"""Execute a frozen fixed grid, retaining every subprocess and original output."""
import argparse
from collections import Counter
import csv
import datetime as dt
import hashlib
import io
import itertools
import json
import platform
from pathlib import Path
import random
import statistics
import struct
import subprocess
import time
ROOT=Path(__file__).resolve().parent
def sha(raw):return hashlib.sha256(raw).hexdigest()
def now():return dt.datetime.now(dt.timezone.utc).isoformat()
def validate_lock(identifier):
 p=ROOT/'protocols'/f'{identifier}.json';lock=json.loads(p.with_suffix('.lock.json').read_text());protocol=json.loads(p.read_text())
 assert sha(p.read_bytes())==lock['protocol_sha256']
 manifest=ROOT/'evidence/implementation-manifest-v1.json';assert sha(manifest.read_bytes())==lock['code_manifest_sha256']
 for name,digest in json.loads(manifest.read_text())['files'].items():assert sha((ROOT/name).read_bytes())==digest,name
 return protocol
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--stage',choices=['main','followup'],required=True);args=parser.parse_args()
 identifier='reliable-'+args.stage+'-v1';p=validate_lock(identifier);out=ROOT/'evidence'/identifier
 if out.exists():raise RuntimeError('Results already exist; do not replace a campaign')
 out.mkdir();rawdir=out/'raw';rawdir.mkdir();inputs=ROOT/'evidence/inputs-v1';inputs.mkdir(exist_ok=True)
 truths={};versions=[]
 for i,skew in enumerate(p['inputs']['skewness']):
  path=inputs/f'zipf-{skew}.bin'
  if not path.exists():
   rng=random.Random(p['inputs']['input_seeds'][i]);keys=rng.choices(range(1,p['inputs']['support']+1),weights=[k**(-skew) for k in range(1,p['inputs']['support']+1)],k=p['inputs']['items']);path.write_bytes(struct.pack('<'+str(len(keys))+'I',*keys))
  data=path.read_bytes();keys=[v[0] for v in struct.iter_unpack('<I',data)];truths[skew]=Counter(keys)
  assert len(keys)==p['inputs']['items'] and min(keys)>0 and max(keys)<=p['inputs']['support']
  versions.append(dict(skewness=skew,path=str(path.relative_to(ROOT)),sha256=sha(data),bytes=len(data),updates=len(keys),distinct=len(truths[skew])))
 grid=list(itertools.product(p['inputs']['skewness'],p['configurations']['budgets_bytes'],p['configurations']['algorithms'],p['inputs']['hash_seeds']))
 random.Random(1004).shuffle(grid);runs=[];start=time.monotonic()
 for index,(skew,budget,algorithm,seed) in enumerate(grid):
  run_id=f'{args.stage}-{index:04}';cfg=f'{args.stage}-zipf{skew}-{budget}-{algorithm}'
  row=dict(id=run_id,configuration_id=cfg,protocol_id=identifier,stage=args.stage,status='complete',reason=None,skewness=skew,budget_bytes=budget,algorithm=algorithm,seed=seed,started_at=now(),ended_at=None,input_sha256=next(v['sha256'] for v in versions if v['skewness']==skew),raw_sha256=None,measurements=None)
  if time.monotonic()-start>p['resources']['max_wall_seconds']:row.update(status='excluded',reason='Frozen campaign wall limit exceeded');runs.append(row);continue
  cmd=[str(ROOT/'build/harness'),algorithm,p['configurations']['budget_regime'],str(budget),str(seed),'25',str(inputs/f'zipf-{skew}.bin'),'unit']
  try:
   call=subprocess.run(cmd,capture_output=True,timeout=45);raw=call.stdout;raw_path=rawdir/f'{run_id}.csv';raw_path.write_bytes(raw);(rawdir/f'{run_id}.stderr').write_bytes(call.stderr)
   row.update(exit_code=call.returncode,raw_sha256=sha(raw),raw_path=str(raw_path.relative_to(ROOT)))
   if call.returncode:raise ValueError(f'Process exit {call.returncode}')
   header,body=raw.decode().split('\n',1);meta=json.loads(header);measurements=list(csv.DictReader(io.StringIO(body)))
   parsed={int(v['key']):dict(truth=int(v['truth']),estimate=int(v['estimate']),lower=int(v['lower']) if v['lower'] else None) for v in measurements}
   expected=dict(truths[skew]);expected.update({0:0,-1:0,1000001:0})
   assert {key:v['truth'] for key,v in parsed.items()}==expected,'Exact key identity/truth mismatch'
   assert len(parsed)==len(measurements),'Duplicate key'
   assert meta['updates']==sum(expected.values()) and meta['distinct_keys']==len(truths[skew])
   errors=[abs(v['estimate']-v['truth']) for v in parsed.values() if v['truth']>0]
   interval=sum(not v['lower']<=v['truth']<=v['estimate'] for v in parsed.values()) if algorithm.startswith('RS') else None
   lost=meta['updates']-meta['filtered_updates']-meta['bucket_mass'] if meta['bucket_mass'] is not None else None
   row['measurements']=dict(**meta,outliers=sum(abs(v['estimate']-v['truth'])>25 for v in parsed.values()),max_absolute_error=max(abs(v['estimate']-v['truth']) for v in parsed.values()),aae=sum(errors)/len(errors),are=sum(abs(v['estimate']-v['truth'])/v['truth'] for v in parsed.values() if v['truth']>0)/len(errors),interval_violations=interval,lost_mass=lost,insert_mups=meta['updates']/meta['insert_seconds']/1e6,query_mqps=meta['updates']/meta['query_seconds']/1e6,queried_keys=len(parsed),underestimates=sum(v['estimate']<v['truth'] for v in parsed.values()),negative_estimates=sum(v['estimate']<0 for v in parsed.values()))
  except Exception as exc:row.update(status='failed',reason=f'{type(exc).__name__}: {str(exc)[:200]}')
  row['ended_at']=now();runs.append(row)
  (out/'runs.json').write_text(json.dumps(runs,indent=2)+'\n')
  if (index+1)%90==0:print(f'{identifier}: {index+1}/{len(grid)} records',flush=True)
 campaign=dict(id=identifier,stage=args.stage,status='complete' if all(r['status']=='complete' for r in runs) else 'partial',protocol_sha256=sha((ROOT/'protocols'/f'{identifier}.json').read_bytes()),runs=runs,inputs=versions,requested_resources=p['resources'],actual_resources=dict(platform=platform.platform(),python=platform.python_version(),processes=1,aws_instances=0),elapsed_seconds=time.monotonic()-start,completed_at=now(),usage=dict(estimated_cloud_compute_usd=0,measured_billing_usd=None,workstation_allocation_usd=None,codex_usage_usd=None))
 (out/'campaign.json').write_text(json.dumps(campaign,indent=2)+'\n');print(json.dumps({'id':identifier,'statuses':dict(Counter(r['status'] for r in runs)),'elapsed_seconds':campaign['elapsed_seconds']}))
if __name__=='__main__':main()
