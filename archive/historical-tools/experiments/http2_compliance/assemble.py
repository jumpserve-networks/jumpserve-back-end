"""Versioned evidence assembly; never replaces frozen main reanalysis outputs."""
from collections import Counter
import datetime
import hashlib
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parent
def read(p):return json.loads((ROOT/p).read_text())
def sha(p):return hashlib.sha256((ROOT/p).read_bytes()).hexdigest()
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def main():
    d=read('results/relational.json');campaign=d['campaign'];cid=campaign['id']
    lock=read('protocol-lock-v2.json')
    assert sha('analyze_v2.py')==lock['analysis_sha256']
    followup=read('protocol-discrepancy-v1.json')
    freeze=ROOT/'protocol-lock-discrepancy-v1.json'
    identity=dict(protocol_sha256=sha('protocol-discrepancy-v1.json'),code_sha256=sha('assemble.py'))
    if freeze.exists():
        old=json.loads(freeze.read_text());assert all(old[k]==identity[k] for k in identity)
    else:freeze.write_text(json.dumps(dict(identity,locked_at=now()),indent=2)+'\n')
    # Independent synthetic control isolates the released bar-chart assignment.
    upstream_accepted={'Accepted':0,'Dropped':1}
    corrected_accepted={'Accepted':1,'Dropped':0}
    h2h1={'Accepted':1,'Dropped':0}
    assert h2h1['Accepted']-upstream_accepted['Accepted']==1
    assert h2h1['Accepted']-corrected_accepted['Accepted']==0
    report=read('evidence/validation.json')
    report['plotting_control']={'same_accepted_outcome':{'released_bar_delta':1,'corrected_delta':0},'mechanism':'summarize_outputs.py create_dual_scope_difference_bar_graph assigns primary accepted outcomes to counts_full["Dropped"]. The line generator and stored differences must be audited separately.','followup_protocol_sha256':identity['protocol_sha256']}
    primary=[s for s in d['summaries'] if s['published_counts'] is not None]
    report['primary_unknown']=sum(s['unknown'] for s in primary)
    report['primary_recoded']=sum(s['recoded'] for s in primary)
    report['main_scope']='57 archived configuration datasets, 7176 designed measurements; paper Table5 subset 15 datasets/1950 cases'
    for s in d['summaries']:
        hist=s['comparisons'].get('historical')
        if hist and s['configuration_id']=='Traefik-3.3.5':hist['assessment']='discrepant';hist['limitation']='Raw 2.2.8 dropped37/error50079 versus stored change dropped12/error500-2. Current raw change dropped90/error500-79. Source of stale or configuration-dependent output is unresolved.'
        trans=s['comparisons'].get('translation')
        if trans:
            published=trans.get('published_accepted_change')
            trans['published_bar_accepted_change']=published
            trans['corrected_accepted_change']=trans['author_count_changes']['accepted']
            trans['correction']='Matched 78 client cases; category mapping corrected in separate follow-up, archived original left unchanged.'
    for claim in d['claims']:
        if claim['claim_id']=='figure7':claim.update(assessment='discrepant',evidence='Nine checked prose deltas agree, but Traefik dropped delta is +90 in currently released raw data versus published +12. Stored author output gives +12.',limitation='Artifact/output inconsistency unresolved; stale summary or configuration drift are hypotheses. No causal attribution to RFC revision.')
        if claim['claim_id']=='figure8':claim.update(assessment='discrepant',evidence='Controlled accepted-to-accepted fixture exposes bar generator misclassification. Published accepted deltas Envoy8, HAproxy5, Nghttpx5, H2O2; corrected matched deltas6,-1,1,1.',limitation='The released code diagnoses numerical category mapping; it does not demonstrate exploitability or reconstruct historic deployments.')
        if claim['claim_id']=='figure4':claim.update(assessment='inconclusive',evidence='All per-test author vectors and Table5 aggregates reproduced; PDF raster cells are not fully independently checked.')
        if claim['claim_id']=='frame-header':claim['evidence']='Paper Section2.1 says12bytes; its Table1 depicts72bits. RFC9113 4.1 requires9octets. Fresh independent raw-frame loopback controls send and parse9octet headers.'
    campaign['provenance']['analysis_version']='http2-assessment-v3'
    campaign['provenance']['followup_protocol']=followup
    campaign['provenance']['validation']=report
    campaign['provenance']['source_inventory_sha256']=sha('literature.json')
    campaign['limitations'].append('Complete main paper review, but supporting full texts may be partial or unavailable; source inventory is explicit.')
    d['sources']=read('literature.json')
    d['protocols']=[]
    for name in ('v1','v2','discrepancy-v1','loopback-pilot-v1','loopback-pilot-v2','loopback-main-v1'):
        file='protocol-'+name+'.json'
        doc=read(file);lockfile='protocol-lock-'+name+'.json'
        saved=read(lockfile) if (ROOT/lockfile).exists() else {}
        d['protocols'].append(dict(id=doc['id'],version=doc['version'],stage=doc['stage'],document=doc,sha256=sha(file),locked_at=saved.get('frozen_at') or saved.get('locked_at'),code_sha256=saved.get('analysis_sha256') or saved.get('runner_sha256') or saved.get('code_sha256')))
    for run in d['runs']:run.update(protocol_id=cid,analysis_version='http2-artifact-reanalysis-v2')
    for failure in read('evidence/failures.json'):
        d['runs'].append(dict(id=failure['id'],campaign_id=cid,configuration_id=None,protocol_id='http2-compliance-artifact-v1' if failure['id'].startswith('analysis') else 'http2-loopback-pilot-v1',stage=failure['stage'],status='failed',reason=failure['reason'],analysis_sha256=failure.get('analysis_sha256'),analysis_version='implementation-pilot-v1',started_at=failure.get('recorded_at'),ended_at=failure.get('recorded_at'),source_path=None,raw_sha256=None,original_execution_at=None,wall_seconds=None,units='no measurements produced'))
    d['followups']=[]
    for stage in ('pilot-v2','main-v1'):
        f=read('evidence/loopback-'+stage+'.json');d['followups'].append(f)
        for row in f['measurements']:
            if 'received_hex' in row:
                assert len(bytes.fromhex(row['received_hex']))==row['received_bytes']
                assert sum(9+v['length'] for v in row['frames'])+len(bytes.fromhex(row['trailing_hex']))==row['received_bytes']
        assert f['planned']==f['recorded']
        controls=[r for r in f['measurements'] if r['case'] in ('valid-get','valid-ping','unknown-frame-ignore')]
        assert all(r['agrees'] is True for r in controls)
    report['loopback']=[dict(id=f['id'],planned=f['planned'],summary=f['summary'],units='error codes dimensionless; bytes; elapsed seconds',limitations=f['protocol']['limitations']) for f in d['followups']]
    (ROOT/'evidence/assessment-v3.json').write_text(json.dumps(report,indent=2)+'\n')
    (ROOT/'results/assessment-v3.json').write_text(json.dumps(d,indent=2)+'\n')
    print(json.dumps(dict(campaign=cid,runs=len(d['runs']),measurements=len(d['measurements']),primary_unknown=report['primary_unknown'],followups=report['loopback'])))
if __name__=='__main__':main()
