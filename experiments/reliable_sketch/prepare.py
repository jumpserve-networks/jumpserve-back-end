"""Build a separate correction and freeze protocols before any campaign execution."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
ROOT=Path(__file__).resolve().parent
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(path,data):
 if path.exists():raise RuntimeError('Refusing to overwrite immutable protocol: '+str(path))
 path.write_text(json.dumps(data,indent=2)+'\n')
def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--build-only',action='store_true');args=parser.parse_args()
 author=ROOT/'sources/author';commit=subprocess.check_output(['git','-C',str(author),'rev-parse','HEAD'],text=True).strip()
 assert commit=='5a83f03c775142401d23a78e7e81b163ddf7b604'
 original=author/'CPU/src/sketch/rs.cpp';corrected=ROOT/'implementation/rs-weighted-v1.cpp'
 source=original.read_text().replace('int add_val = min(v, mf_err_bound - min_val);','int add_val = min(f, mf_err_bound - min_val);').replace('bkt += add_val;','bkt = max(bkt, min_val + add_val);')
 corrected.write_text(source)
 if not args.build_only:(ROOT/'implementation/correction-v1.patch').write_text(subprocess.run(['diff','-u',str(original),str(corrected)],capture_output=True,text=True).stdout)
 build=ROOT/'build';build.mkdir(exist_ok=True)
 compiler=subprocess.check_output(['c++','--version'],text=True)
 for version,path in [('original',original),('corrected',corrected)]:
  cmd=['c++','-O2','-std=c++11','-I',str(author/'CPU/include'),str(ROOT/'implementation/harness.cpp'),str(path),str(author/'CPU/src/sketch/cm.cpp'),str(author/'CPU/src/sketch/cu.cpp'),'-o',str(build/('harness' if version=='original' else 'harness-corrected'))]
  r=subprocess.run(cmd,capture_output=True,text=True);(build/f'compile-{version}.txt').write_text(r.stdout+r.stderr)
  if r.returncode:raise RuntimeError('Compilation failed; see retained log')
 if args.build_only:
  manifest=json.loads((ROOT/"evidence/implementation-manifest-v1.json").read_text())["files"]
  for path,digest in manifest.items():
   if sha(ROOT/path)!=digest:raise RuntimeError("Frozen implementation differs: "+path)
  print("Rebuilt frozen original and separate corrected executable; protocol/manifest preserved")
  return
 manifest={str(p.relative_to(ROOT)):sha(p) for p in sorted(author.rglob('*')) if p.is_file() and '.git' not in p.parts}
 manifest.update({str(p.relative_to(ROOT)):sha(p) for p in [ROOT/'implementation/harness.cpp',corrected,ROOT/'check_controls.py',ROOT/'run.py']})
 save(ROOT/'evidence/implementation-manifest-v1.json',dict(commit=commit,files=manifest,compiler=compiler,flags=['-O2','-std=c++11'],correction='Weighted filter uses f, and saturating conservative updates use max(counter,min+accepted). Unit path remains original.'))
 common=dict(version=1,locked_at=dt.datetime.now(dt.timezone.utc).isoformat(),artifact_commit=commit,implementation_manifest_sha256=sha(ROOT/'evidence/implementation-manifest-v1.json'),comparison='Exact ground-truth counts; outlier abs(error)>25. Equal input SHA, key identity, budget regime and seed for contrasts. No threshold tuning.',missing='Failed runs retained with reason; no replacement seeds. Missing numbers null, zero explicit. Invalid input/parse/coverage is software failure. Scientific outliers and interval violations are findings.',statistics='Run is replication unit. Ten preselected hash-seed runs on one fixed stream per skewness; keys and timing queries dependent. Report median and min/max descriptive range. No CI, p-value, population failure rate or causal estimate.',resources=dict(host='local macOS arm64 workstation; actual platform recorded per campaign',requested_processes=1,aws_instances=0,max_wall_seconds=1800,max_run_seconds=45,max_original_input_bytes=3000000,max_retained_bytes=200000000,incremental_cloud_compute_usd=0,allocated_workstation_cost_usd=None,model_cost_usd=None),stopping='Execute fixed grid once; stop after 1800 seconds or resource/hash failure; preserve all completed/failed/excluded records. Never retry a seed silently.',inapplicable=['Orbital epochs, geography, deployed terminal counts and link propagation/throughput tests: stream counting domain.','Archived data reanalysis: no authors result datasets/plot tables are released.','Protocol compliance: not a network protocol conformance paper.'],limits=['Not the paper’s original real-world traces or 32M-item synthetic input.','No FPGA VC709 or Tofino/SDE; synthesis, switch accuracy and line rate remain untested.','Fixed observed-key census plus three absent probes is not an unlimited-key/domain guarantee.','Formal theorem uses stronger constants, hash assumptions and SpaceSaving fallback; empirical CPU configuration excludes fallback.','Timing on one machine cannot replicate i9-10980XE results or operational effectiveness.'])
 for stage,regime in [('main','nominal'),('followup','allocated')]:
  protocol=dict(common,id='reliable-'+stage+'-v1',stage=stage,question='How does released CPU accuracy behave under '+regime+' memory budgets?',hypotheses=['All observed keys and absent probes have error<=25 when insertion mass is retained; violations remain recorded.','MPE interval includes exact truth; violations are investigated, not excluded.','Allocated counter memory differs from reported packed memory for ReliableSketch.'],metrics={'outliers':'keys','max_absolute_error':'count units','AAE':'count units/key','ARE':'dimensionless on positive truth only','interval_violations':'keys; null for baselines without bounds','lost_mass':'unit updates','insert_throughput':'million updates/s','query_throughput':'million repeated key queries/s','memory':'bytes, explicitly nominal or allocated counters; exclude metadata/allocator/RSS'},configurations=dict(algorithms=['RS','RS_raw','CM3','CM16','CU3','CU16'],budgets_bytes=[8192,32768,131072],budget_regime=regime,lambda_count_units=25,r_w=2.0,r_lambda=2.5,max_layers=20,filter_fraction=0.2,filter_threshold=3,filter_hashes=2),inputs=dict(generator='Python random.Random.choices weighted finite support 1..4096, w(k)=k**(-skew); uint32 little endian, positive unit updates',items=250000,support=4096,skewness=[0.3,1.2,3.0],input_seeds=[2026100401,2026100402,2026100403],hash_seeds=list(range(1,11)),versions='Python version retained; exact stream bytes+hashes authoritative'),sample_size='3 skewness x 3 budgets x 6 algorithms x 10 fixed hash seeds =540 runs. Bounded seed sensitivity, not powered validation of Delta<1e-10.',controls='Exact Counter truth and independent CSV aggregate recomputation; bucket state enumeration and weighted controls separately frozen; main original unit API only.',sensitivity='Filtered/raw, 3/16 rows, memory budgets, skewness; equal-allocated follow-up predeclared separately.')
  save(ROOT/'protocols'/f'{protocol["id"]}.json',protocol)
 for stage in ['pilot','main']:
  save(ROOT/'protocols'/f'reliable-controls-{stage}-v1.json',dict(common,id='reliable-controls-'+stage+'-v1',stage='pilot' if stage=='pilot' else 'independent correctness',question='Do weighted filter, printed lock order and finite bucket invariants behave as specified?',inputs='Keys1/2/10, weight5; pilot seed1, main seeds1..5; bucket length<=5, three keys and positive weights1..3.',metrics='Exact estimates, bounds, mass and interval inclusion; no statistical inference.',publication_criterion='Report every control, including mismatches. A released-code bug is not proof of published generating code.'))
 for path in sorted((ROOT/'protocols').glob('*.json')):
  save(path.with_suffix('.lock.json'),dict(protocol_sha256=sha(path),code_manifest_sha256=sha(ROOT/'evidence/implementation-manifest-v1.json')))
 print(json.dumps({'frozen_protocols':4,'author_files':len(manifest),'compiler':compiler.splitlines()[0]}))
if __name__=='__main__':main()
