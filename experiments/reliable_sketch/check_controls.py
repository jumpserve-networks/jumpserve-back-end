"""Independent small-state and controlled author-code discrepancy checks."""
import argparse
import datetime as dt
import hashlib
import itertools
import json
from pathlib import Path
import subprocess
ROOT=Path(__file__).resolve().parent
def bucket_check():
 checks=0;states=0
 # Every positive weighted stream over three keys through length five, all prefixes.
 def visit(identifier,yes,no,truth,depth):
  nonlocal checks,states
  states+=1
  assert yes+no==sum(truth.values())
  for key in range(4):
   estimate=yes if key==identifier else no
   assert estimate-no<=truth.get(key,0)<=estimate
   checks+=1
  if depth==5:return
  for key,weight in itertools.product(range(1,4),range(1,4)):
   a,b,c=identifier,yes,no
   if a==key:b+=weight
   else:c+=weight
   if c>=b:a=key;b,c=c,b
   t=dict(truth);t[key]=t.get(key,0)+weight
   visit(a,b,c,t,depth+1)
 visit(None,0,0,{},0)
 return dict(states=states,interval_checks=checks,conservation_checks=states,status='passed',scope='Finite bucket state enumeration, length<=5, keys1..3, weights1..3; unseen key0 checked. No hash or finite-layer failure-probability validation.')
def main():
 p=argparse.ArgumentParser();p.add_argument('--stage',choices=['pilot','main'],required=True);a=p.parse_args()
 result=dict(id='reliable-controls-'+a.stage+'-v1',stage=a.stage,at=dt.datetime.now(dt.timezone.utc).isoformat(),independent_bucket=bucket_check())
 result['weighted']={}
 for version in ('original','corrected'):
  executable=ROOT/'build'/('harness' if version=='original' else 'harness-corrected')
  result['weighted'][version]=[]
  for seed,key in itertools.product(range(1,6) if a.stage=='main' else [1],[1,2,10]):
   call=subprocess.run([str(executable),'RS','nominal','65536',str(seed),'25',str(key),'weighted'],capture_output=True,text=True)
   result['weighted'][version].append(dict(seed=seed,status='complete' if call.returncode==0 else 'failed',exit_code=call.returncode,stdout=call.stdout,stdout_sha256=hashlib.sha256(call.stdout.encode()).hexdigest(),parsed=json.loads(call.stdout) if call.returncode==0 else None))
 # Literal Algorithm1 lines10–11 overwrite NO before computing the remainder.
 result['pseudocode_control']=dict(before={'yes':5,'no':0,'lambda':2},arriving_weight=4,literal={'new_no':2,'forwarded':4,'added_mass':6},saved_old_no={'new_no':2,'forwarded':2,'added_mass':4},status='demonstrated',limitation='Arithmetic reading of printed pseudocode; prose and released CPU path preserve the remainder. Does not establish published generating revision.')
 result['confidence_control']=dict(individual_success=0.95,two_shared_event_success=0.95,product=0.9025,union_bound_lower=0.90,status='demonstrated',limitation='Probability counterexample to unconditional multiplication, not a measured failure probability for ReliableSketch.')
 out=ROOT/'evidence'/f'controls-{a.stage}-v1.json'
 if out.exists():raise RuntimeError('Control output already exists; create a new protocol/version')
 out.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k!='weighted'}))
if __name__=='__main__':main()
