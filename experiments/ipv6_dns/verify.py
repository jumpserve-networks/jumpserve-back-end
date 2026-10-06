"""Mechanical checks of frozen scientific evidence; scientific discrepancies are findings."""
import collections,hashlib,json,math,platform,sys
from pathlib import Path
R=Path(__file__).resolve().parent
def main():
 d=json.loads((R/'evidence/assessment-v2.json').read_text());checks=[]
 for p in d['protocols']:
  raw=(R/f'protocols/{p["id"]}.json').read_bytes();assert hashlib.sha256(raw).hexdigest()==p['sha256']==json.loads((R/f'protocols/{p["id"]}.lock.json').read_text())['sha256']
 checks.append('Original protocol locks unchanged')
 assert len(d['runs'])==158 and len(set(r['id'] for r in d['runs']))==158
 assert collections.Counter(r['status'] for r in d['runs'])==dict(complete=145,failed=2,excluded=11)
 assert len(d['measurements'])==136512 and len({(m['run_id'],m['configuration_id'],m['metric']) for m in d['measurements']})==136512
 for m in d['measurements']:
  if m['status']=='recorded':
   assert m['denominator']>0 and m['numerator']>=0 and math.isfinite(m['value']) and abs(m['value']-100*m['numerator']/m['denominator'])<1e-10
  else:assert m['value'] is None and m['reason']
 checks.append('Complete observation identities, count units, arithmetic and missing-value invariants')
 assert len(d['comparisons'])==1152 and all(c['assessment']=='reproduced' and abs(c['difference_pp'])<=.0051 for c in d['comparisons'])
 assert d['validation']['original_fold_disagreements']==0
 assert next(c for c in d['controls'] if c['id']=='empty-duration')['status']=='failed'
 assert {s['reference_number'] for s in d['sources']}==set(range(74))
 checks.append('Published and reproduced values remain separate; original fold agrees; failed control retained; direct-source inventory complete in topology, incomplete in review')
 for m in d['manifest']:assert hashlib.sha256((R/m['path']).read_bytes()).hexdigest()==m['sha256']
 checks.append('Every original manifest byte hash verified locally; private storage round-trip verification is a separate report')
 report=dict(id='ipv6-scientific-validation-v1',passed=True,checks=checks,counts={k:len(d[k]) for k in ['runs','measurements','configurations','published','comparisons','sources','claims']},assessment_sha256=hashlib.sha256((R/'evidence/assessment-v2.json').read_bytes()).hexdigest(),python=sys.version,platform=platform.platform(),shared_assumptions='Mechanical arithmetic checks reuse parsed evidence; original fold and reanalysis share author summaries. Neither independently validates upstream packet aggregation or causal claims.',coverage_limits='Partial main/direct literature review; unavailable APNIC/raw-pcap/historical routes; no new empirical network measurements, simulation or inferential CI.')
 p=R/'evidence/scientific-validation-v1.json';assert not p.exists();p.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':main()
