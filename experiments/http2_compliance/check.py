"""Independent arithmetic, completeness and immutable-input checks; no network."""
from collections import Counter
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    d=json.loads((ROOT/'results/relational.json').read_text())
    p=json.loads((ROOT/'published-values.json').read_text())
    lock=json.loads((ROOT/'protocol-lock-v2.json').read_text())
    assert digest(ROOT/'analyze_v2.py')==lock['analysis_sha256']
    assert digest(ROOT/'protocol-v2.json')==lock['protocol_sha256']
    for path,sha in lock['files'].items(): assert digest(ROOT/'sources/HTTP2-Compliance-Tests'/path)==sha,path
    assert len(d['runs'])==57 and len(d['measurements'])==7176
    assert len({(m['run_id'],m['test_id']) for m in d['measurements']})==7176
    by={s['configuration_id']:s for s in d['summaries']}
    checks=[]
    for s in d['summaries']:
        rows=[m for m in d['measurements'] if m['run_id']==s['run_id']]
        assert len(rows)==s['planned']
        assert sum(s['author_counts'].values())==s['planned']
        assert sum(s['preserved_counts'].values())==s['planned']
        assert Counter(m['outcome'] for m in rows)==s['preserved_counts']
        assert all(m['preserved_rule_conformant'] is None for m in rows if m['outcome']=='unknown')
        assert all(m['scope_compatible'] for m in rows if m['expected_scope']=='stream' and m['observed_scope']=='connection')
    for cfg,expected in p['table5'].items():
        assert all(by[cfg]['author_counts'].get(k,0)==v for k,v in expected.items()),cfg
    total=Counter()
    for cfg in p['table5']: total.update(by[cfg]['author_counts'])
    assert sum(total.values())==1950
    assert total['dropped']==856
    assert sum(total[k] for k in ('received','modified','unmodified'))==205
    assert sum(total[k] for k in ('dropped','500','goaway','reset'))==1745
    for cfg in p['figure5_percent']: assert by[cfg]['comparisons']['figure5_agreement'],cfg
    for cfg in p['figure6_percent']: assert by[cfg]['comparisons']['figure6_agreement'],cfg
    for category,proxies in p['figure7_prose_changes'].items():
        for proxy,value in proxies.items():
            s=next(by[cfg] for cfg in p['table5'] if cfg.startswith(proxy+'-'))
            observed=s['comparisons']['historical']['author_count_changes'][category]
            checks.append(dict(figure=7,proxy=proxy,category=category,published=value,reproduced=observed,agrees=observed==value))
    for proxy,value in p['figure8_prose_accepted_changes'].items():
        s=next(by[cfg] for cfg in p['table5'] if cfg.startswith(proxy+'-'))
        observed=s['comparisons']['translation']['author_count_changes']['accepted']
        checks.append(dict(figure=8,proxy=proxy,category='accepted',published=value,reproduced=observed,agrees=observed==value))
    report=dict(status='pass',runs=57,measurements=7176,table5_rows=15,figure5_configurations=10,figure6_configurations=10,unknown=sum(s['unknown'] for s in d['summaries']),recoded=sum(s['recoded'] for s in d['summaries']),historical_and_translation=checks,limitations=['Independent arithmetic checks reuse the archived worker evidence; they are not independent physical proxy measurements.','Figures 7–8 checks cover numerical statements transcribed from prose, not every raster bar.','No inferential confidence intervals: the designed suite is a census, not a probability sample.'])
    (ROOT/'evidence/validation.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
if __name__=='__main__': main()
