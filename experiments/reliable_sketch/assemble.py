"""Versioned analysis and relational public evidence bundle, preserving paper values separately."""
from collections import Counter,defaultdict
import csv
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import statistics
import struct
ROOT=Path(__file__).resolve().parent
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def load(path):return json.loads(path.read_text())
def main():
 output=ROOT/'evidence/assessment-v1.json'
 if output.exists():raise RuntimeError('Assessment already exists; use a new version')
 bundle=dict(protocols=[],campaigns=[],configurations=[],runs=[],summaries=[],sources=load(ROOT/'literature.json'),claims=[],published=load(ROOT/'published-values.json'),controls=[],measurements=[],manifest=[])
 metrics={'outliers':'keys','max_absolute_error':'count units','aae':'count units/key','are':'dimensionless','interval_violations':'keys','lost_mass':'unit updates','insert_mups':'million updates/s','query_mqps':'million repeated queries/s','allocated_counter_bytes':'bytes','nominal_bytes':'bytes','queried_keys':'keys','underestimates':'keys','negative_estimates':'keys','filtered_updates':'unit updates','bucket_mass':'unit updates','insert_seconds':'seconds','query_seconds':'seconds'}
 validation=dict(id='reliable-validation-v1',at=dt.datetime.now(dt.timezone.utc).isoformat(),raw_files_checked=0,raw_hashes_passed=0,key_identity_checks=0,independent_aggregate_checks=0,findings={},limitations=['Independent aggregate arithmetic shares retained inputs, public CSV and specified metrics. It does not independently implement the full sketch/hash or validate the theorem.'])
 for path in sorted((ROOT/'protocols').glob('*.json')):
  if '.lock.' in path.name:continue
  lock=load(path.with_suffix('.lock.json'));assert digest(path)==lock['protocol_sha256']
  p=load(path);bundle['protocols'].append(dict(id=p['id'],version=p['version'],stage=p['stage'],document=p,sha256=digest(path),locked_at=p['locked_at']))
 for stage in ['main','followup']:
  campaign=load(ROOT/'evidence'/f'reliable-{stage}-v1/campaign.json');identifier=campaign['id'];p=load(ROOT/'protocols'/f'{identifier}.json');runs=campaign.pop('runs');groups=defaultdict(list)
  campaign.update(title='ReliableSketch '+stage+' CPU campaign',planned_runs=540,recorded_runs=len(runs),protocol_id=identifier,analysis_sha256=digest(Path(__file__)),analysis_version='reliable-assessment-v1',experiment_type='new CPU measurements on generated synthetic streams',limitations=p['limits'],provenance=dict(artifact_commit=p['artifact_commit'],input_versions=campaign.pop('inputs'),analysis_sha256=digest(Path(__file__))))
  bundle['campaigns'].append(campaign)
  for row in runs:
   row['campaign_id']=identifier;row['analysis_version']='reliable-assessment-v1';row['analysis_sha256']=digest(Path(__file__));groups[row['configuration_id']].append(row)
   if row['status']=='complete':
    raw_path=ROOT/row['raw_path'];assert digest(raw_path)==row['raw_sha256'];validation['raw_files_checked']+=1;validation['raw_hashes_passed']+=1
    raw=raw_path.read_text();head,body=raw.split('\n',1);records=list(csv.DictReader(io.StringIO(body)))
    observed={int(r['key']):int(r['truth']) for r in records};truth=Counter(v[0] for v in struct.iter_unpack('<I',(ROOT/'evidence/inputs-v1'/f'zipf-{row["skewness"]}.bin').read_bytes()));truth.update({0:0,-1:0,1000001:0});assert observed==dict(truth);validation['key_identity_checks']+=1
    assert len(observed)==len(records)
    count_bad=sum(abs(int(r['estimate'])-int(r['truth']))>25 for r in records)
    max_err=max(abs(int(r['estimate'])-int(r['truth'])) for r in records)
    interval=sum(not int(r['lower'])<=int(r['truth'])<=int(r['estimate']) for r in records) if row['algorithm'].startswith('RS') else None
    assert row['measurements']['outliers']==count_bad and row['measurements']['max_absolute_error']==max_err and row['measurements']['interval_violations']==interval
    positive=[r for r in records if int(r['truth'])>0]
    independent_aae=statistics.mean(abs(int(r['truth'])-int(r['estimate'])) for r in positive)
    assert abs(independent_aae-row['measurements']['aae'])<1e-9;validation['independent_aggregate_checks']+=4
    for metric,units in metrics.items():
     value=row['measurements'].get(metric);bundle['measurements'].append(dict(run_id=row['id'],metric=metric,value=value,status='recorded' if value is not None else 'missing',reason=None if value is not None else 'Not exposed/applicable to this baseline',units=units))
   bundle['runs'].append(row)
  for configuration_id,replicates in sorted(groups.items()):
   sample=replicates[0];quantities=[r['measurements'] for r in replicates if r['status']=='complete'];first=quantities[0] if quantities else {}
   bundle['configurations'].append(dict(id=configuration_id,campaign_id=identifier,algorithm=sample['algorithm'],skewness=sample['skewness'],budget_bytes=sample['budget_bytes'],budget_regime=p['configurations']['budget_regime'],input_sha256=sample['input_sha256'],details=p['configurations'],requested_resources=campaign['requested_resources'],actual_resources=dict(campaign['actual_resources'],nominal_bytes=first.get('nominal_bytes'),allocated_counter_bytes=first.get('allocated_counter_bytes'),bucket_size=first.get('bucket_size'))))
   stats={}
   for metric in metrics:
    values=[q[metric] for q in quantities if q.get(metric) is not None]
    stats[metric]=dict(n=len(values),median=statistics.median(values) if values else None,min=min(values) if values else None,max=max(values) if values else None,units=metrics[metric])
   bundle['summaries'].append(dict(configuration_id=configuration_id,campaign_id=identifier,planned=10,recorded=len(quantities),failed=sum(r['status']=='failed' for r in replicates),excluded=sum(r['status']=='excluded' for r in replicates),statistics=stats,interval_kind='descriptive seed range, not a confidence interval',observed_keys=first.get('distinct_keys'),query_population=first.get('query_population'),zero_outlier_runs=sum(q['outliers']==0 for q in quantities),lossless_runs=sum(q.get('lost_mass')==0 for q in quantities) if sample['algorithm'].startswith('RS') else None,interval_valid_runs=sum(q.get('interval_violations')==0 for q in quantities) if sample['algorithm'].startswith('RS') else None))
 for stage in ['pilot','main']:
  control=load(ROOT/'evidence'/f'controls-{stage}-v1.json');bundle['controls'].append(control)
 def claim(identifier,location,description,assessment,evidence,limitation,check,required,feasibility):
  bundle['claims'].append(dict(id=identifier,location=location,description=description,assessment=assessment,evidence=evidence,limitation=limitation,proposed_check=check,required_inputs=required,feasibility=feasibility))
 claim('bucket','p4 §3.1, Figs1–2','Error-sensible bucket bounds','reproduced','66,430 finite states; 265,720 interval checks and conservation checks pass.','Independent positive-weight bucket only; full sketch guarantee is separate.','Finite weighted stream enumeration','positive keys and weights','completed under declared finite scope')
 claim('lock-order','p5 Algorithm1 lines10–11, Fig3','Weighted lock remainder','discrepant','Literal overwrite forwards4 after accommodating2 from a weight4 update; correct saved-old-NO remainder is2.','Printed pseudocode issue; accompanying prose and CPU splitting differ.','Arithmetic state transition control','pre-lock state YES5 NO0 lambda2','completed')
 claim('weighted-code','§3.1–3.2; released CPU/src/sketch/rs.cpp weighted API','Released weighted filter behavior','discrepant','Keys1/2 with weight5 return1/2 in original across five seeds; separate corrected version returns5. Key10 control agrees.','Defect in released 2024 revision does not identify published 2025 generating code; correction coverage limited.','Original/corrected one-update controlled contrast','pinned artifact, positive weights','completed')
 claim('confidence-product','p2 §1; Table1','Product of per-key confidence','discrepant','Shared success event with marginal0.95 has joint0.95, while product is0.9025.','Counterexample to unconditional equality; not a disproof of ReliableSketch theorem.','Probability dependence control','shared-event construction','completed')
 claim('theory','§4, Table1–2, AppendixA.1–A.5','Overall probability and complexity theorems','inconclusive','The stated theorem includes stronger constants, uniform independent hashes and auxiliary SpaceSaving. CPU experiments use heuristic parameters and no fallback.','Concentration-conditioning proof steps are not fully independently verified; finite runs cannot test Delta<1e-10.','Independent mathematical proof audit','all proof steps and hash assumptions','partial reading; proof verification incomplete')
 claim('memory','p9 §6.1.1; Figs4–5','Same small CPU memory','discrepant','sizeof(Bucket)=16 versus10 accounted bytes; filter uses32-bit counters versus2 accounted bits. Nominal RS128KiB allocates587,152 counter bytes.','Allocated counters exclude metadata/allocator/RSS. Does not negate packed hardware feasibility or establish published revision.','Nominal versus actual array-byte accounting and fixed-budget follow-up','pinned compiler/platform, author arrays','completed on macOS arm64')
 claim('accuracy','§6.2.1, Figs4–6','Original dataset outliers and memory minima','untested','Original trace identities, bytes, plotting vectors and original seed draws not released. New three-skew CPU grids are separate.','No numerical reproduction of IP/Web/university/Hadoop minima; smaller synthetic study is not32M paper experiment.','Repeat original input byte hashes and all baselines','author traces, transformations, result vectors','unavailable dependencies')
 claim('average-error','§6.2.2, Figs7–8','Original AAE/ARE comparisons','untested','New AAE/ARE measured and independently reaggregated for matched fixed synthetic inputs.','Exact paper configurations/data unavailable; not numerical agreement with curves.','Equivalent-input/budget algorithm contrasts','original traces and all seven baseline configurations','new bounded comparison only')
 claim('speed','§6.3 Fig9','CPU throughput values','untested','New single-host insertion/query timings retained; query timing uses250k repeated traffic-key queries.','Different CPU, input, query distribution and operation counts; no claim to reproduce25.40/51.29Mpps.','Matched target-server timing','i9-10980XE, 10M input and query sequence','unavailable original conditions')
 claim('parameters','§6.4, Figs10–14','Recommended ratios and memory optimization','untested','Fixed R_w2 and R_lambda2.5; memory, skew, filter/raw and row-count sensitivity executed.','No full ratio grid or best-case memory search. Paper MB convention unspecified.','Ratio and threshold sweep under locked budgets','original traces and search protocol','limited applicable sensitivity')
 claim('parameter-direction','p11 §6.4.2 Fig13','R_lambda direction in same-AAE comparison','inconclusive','Prose says larger R_lambda uses less memory for low R_w; displayed curves generally rise.','Visual internal tension; exact numerical curves and intended wording unavailable.','Recover plotting data / author clarification','original Figure13 values','visual review only')
 claim('hash-calls','§6.4.4 Fig15','Average hash work','untested','Released filter unit insertion hashes twice to read and twice to update; query_hash reports query path.','Asymptotic work versus literal primitive invocation needs instrumented count; new throughput does not reconstruct plot.','Instrument primitive hash calls on exact input','original instrumentation and traces','not executed')
 claim('sensing','§6.5.1–2, Figs16–18','Finite sketch error sensing and control','inconclusive','Lossless main RS variants:120 runs, zero interval violations. Small budgets exhibit lost mass and invalid intervals; fixed allocation follow-up preserves them.','No full-universe guarantee or calibrated all-key failure probability. Dropped mass is an admitted insertion-failure boundary.','Ground truth interval checks and mass accounting','same stream identities and finite arrays','completed finite empirical scope')
 claim('fpga','§5.1 Table3','FPGA frequency and resources','untested','Table reports339MHz; prose rounds to340. Four-page artifact supplement retained.','VC709/Vivado and actual synthesis reports unavailable; one item/cycle is conditional architecture arithmetic.','Rebuild/synthesize and measure device','VC709, licensed tooling and platform','unavailable equipment')
 claim('tofino','§5.2 Table4; §6.5.3 Fig19','Switch resources and line-rate accuracy','untested','P4 source retained; paper SRAM/AAE units retained separately.','No Wedge100BF/Tofino/SDE or40Gbps testbed. Kbps results not CPU count units.','Compile, deploy and replay owned exact trace','switch/SDE,40M packet traces, rate normalization','unavailable equipment/inputs')
 claim('operational','§1, §7–8','Years of no outliers, practical and general benefits','untested','No longitudinal deployment or application-effectiveness measurement.','Positive finite-stream checks and numerical agreement do not establish operation over years, adversarial hashing, causality or generalization.','Independent longitudinal operational evaluation','application workloads, failure horizon and causal controls','outside bounded campaign')
 rs=[r for r in bundle['runs'] if r['algorithm'].startswith('RS')];main_rs=[r for r in rs if r['stage']=='main'];lossless=[r for r in main_rs if r['measurements']['lost_mass']==0]
 validation['findings']=dict(total_cpu_runs=len(bundle['runs']),source_records=len(bundle['sources']),claims=len(bundle['claims']),lossless_main_RS_runs=len(lossless),lossless_main_interval_violations=sum(r['measurements']['interval_violations'] for r in lossless),main_RS_runs_with_loss=sum(r['measurements']['lost_mass']>0 for r in main_rs),main_RS_runs_with_outliers=sum(r['measurements']['outliers']>0 for r in main_rs),all_cpu_statuses=dict(Counter(r['status'] for r in bundle['runs'])))
 bundle['validation']=validation
 bundle['review_definition']='Complete: identity/version checked, all text including appendices plus every figure/table examined and section-specific notes retained. Retrieval/hash verification is separate. Full proof correctness and full numerical reproduction are separate judgments. Partial notes explicitly identify unread sections; unreviewed means no substantive content inspection; unavailable means cited full text not retrieved.'
 bundle['label_definitions']={'reproduced':'Declared check agrees under stated conditions; scope is explicit, not global validation.','discrepant':'Recorded mismatch in a matched check or independently demonstrated internal/code statement.','inconclusive':'Evidence cannot decide the major claim; dependency or reasoning coverage incomplete.','untested':'No experiment evaluates the claim under equivalent conditions.'}
 bundle['analysis_version']='reliable-assessment-v1';bundle['created_at']=dt.datetime.now(dt.timezone.utc).isoformat()
 original_paths=[p for p in (ROOT/'sources').rglob('*') if p.is_file() and '.git' not in p.parts and p.suffix not in {'.txt'}]
 original_paths.extend(p for p in (ROOT/'evidence').rglob('*') if p.is_file() and 'pages' not in p.parts and p.name not in {'assessment-v1.json','validation-v1.json'})
 for p in sorted(set(original_paths)):bundle['manifest'].append(dict(path=str(p.relative_to(ROOT)),sha256=digest(p),bytes=p.stat().st_size,visibility='private original bytes'))
 (ROOT/'evidence/validation-v1.json').write_text(json.dumps(validation,indent=2)+'\n');output.write_text(json.dumps(bundle,indent=2)+'\n')
 print(json.dumps(validation['findings'],indent=2))
if __name__=='__main__':main()
