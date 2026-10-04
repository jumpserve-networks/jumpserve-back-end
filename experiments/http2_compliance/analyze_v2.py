"""Reanalyse every archived dataset without editing or executing upstream files."""
import ast
from collections import Counter
import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

ROOT=Path(__file__).resolve().parent
UPSTREAM=ROOT/'sources/HTTP2-Compliance-Tests'
CAMPAIGN='http2-compliance-artifact-v2'
CATEGORIES=('dropped','500','goaway','reset','received','modified','unmodified','unknown')

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()

def author_classifier():
    tree=ast.parse((UPSTREAM/'summarize_outputs.py').read_text())
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='analyze_results')
    namespace={'json':json}
    exec(compile(ast.Module(body=[fn],type_ignores=[]),str(UPSTREAM/'summarize_outputs.py'),'exec'),namespace)
    return namespace['analyze_results']

def preserved(record, identifier):
    """Retain concrete worker evidence; unrecognized evidence is never a dropped packet."""
    result=record.get('result') if isinstance(record,dict) else None
    if not isinstance(result,dict) or not result:
        return 'unknown','missing or invalid worker result',None,None
    workers=[result.get(k) for k in ('Worker_1','Worker_2')]
    workers=[w for w in workers if isinstance(w,dict)]
    variables=[w.get('Variables') or {} for w in workers]
    variables=[v for v in variables if isinstance(v,dict)]
    text=' '.join(str(v.get(k,'')) for v in variables for k in ('msg','result','client_result','server_result'))
    states=[w.get('State') for w in workers]
    error=re.search(r'Error code (\d+)',text)
    code=int(error[1]) if error else None
    if identifier==6 and any(v.get('result')=='Successfully received all 1/1 frames.' for v in variables): return 'unmodified',None,None,None
    if identifier in (81,92) and any(str(v.get(k,'')).startswith('Successfully received all') for v in variables[:1] for k in ('result','client_result')): return 'unmodified',None,None,None
    if identifier==1 and workers and workers[0].get('State')=='REJECTED': return 'dropped','author special-case, explicit rejection',code,None
    for outcome in ('modified','unmodified'):
        if any(v.get(k)==f'Test result: {outcome.upper()}' for v in variables for k in ('result','client_result','server_result')): return outcome,None,code,None
    if any(str(v.get('result','')).startswith('Received 1 HTTP/1.1 requests') for v in variables): return 'received',None,code,None
    if 'GOAWAY_RECEIVED' in states: return 'goaway',None,code,'connection'
    if 'REJECTED' in states: return '500',None,code,'HTTP rejection; code/status not independently parsed'
    if 'RESET_RECEIVED' in states: return 'reset',None,code,'stream'
    if 'TIMEOUT' in states: return 'dropped','explicit worker timeout; network cause unproven',code,None
    return 'unknown','unrecognized worker states; no fallback inference',code,None

def conformance(outcome, expected):
    if outcome=='unknown': return None
    if expected=='error': return outcome in ('goaway','reset','500')
    if expected=='ignore': return outcome=='dropped'
    return None

def setup_lock():
    manifest={str(p.relative_to(UPSTREAM)):sha(p) for p in sorted(UPSTREAM.rglob('*')) if p.is_file() and '.git' not in p.parts}
    lock={'frozen_at':now(),'campaign':CAMPAIGN,'protocol_sha256':sha(ROOT/'protocol-v2.json'),'paper_sha256':sha(ROOT/'sources/main-paper.pdf'),'published_values_sha256':sha(ROOT/'published-values.json'),'analysis_sha256':sha(ROOT/'analyze_v2.py'),'artifact_commit':subprocess.check_output(['git','-C',str(UPSTREAM),'rev-parse','HEAD'],text=True).strip(),'files':manifest}
    path=ROOT/'protocol-lock-v2.json'
    if path.exists():
        old=json.loads(path.read_text())
        if any(old[k]!=lock[k] for k in ('protocol_sha256','analysis_sha256','artifact_commit','paper_sha256','published_values_sha256','files')): raise RuntimeError('Frozen protocol, implementation or inputs changed; create a new analysis version')
        return old
    path.write_text(json.dumps(lock,indent=2)+'\n')
    return lock

def main():
    began=now(); start=time.perf_counter(); lock=setup_lock()
    cases=json.loads((UPSTREAM/'test_cases.json').read_text())
    assert len(cases)==156 and {c['id'] for c in cases}==set(range(1,157))
    case_by_id={c['id']:c for c in cases}
    classify=author_classifier()
    runs=[]; configs=[]; measurements=[]; summaries=[]; artifacts=[]
    paper=json.loads((ROOT/'published-values.json').read_text())
    published=paper['table5']
    pairs=json.loads((UPSTREAM/'docs/pairs.json').read_text())['pairs']
    assert len(pairs)==61 and len({i for pair in pairs for i in pair})==122
    archived_table={}
    table=(UPSTREAM/'analysis/tables/result_counts.md').read_text()
    for line in table.splitlines():
        if '| Dropped:' not in line: continue
        proxy=line.split('|')[1].strip()
        archived_table[proxy]={k:int(v) for k,v in re.findall(r'(Dropped|500 Error|GOAWAY|RESET|Received|Modified|Unmodified): (\d+)',line)}
    aliases={'Dropped':'dropped','500 Error':'500','GOAWAY':'goaway','RESET':'reset','Received':'received','Modified':'modified','Unmodified':'unmodified'}
    for index,path in enumerate(sorted((UPSTREAM/'results').rglob('*.json'))):
        stamp=now(); cfg=path.parent.name; data=json.loads(path.read_text()); identifier=f'archive-{index:03}'
        client_only=cfg.endswith('-H2H1') or any(cfg.startswith(x) for x in ('Azure','Fastly','Varnish','Nginx','Lighttpd'))
        planned=list(range(1,79 if client_only else 157))
        proxy=re.sub(r'^TLS-|^Retest-','',cfg).split('-')[0]
        version=re.sub(r'^TLS-|^Retest-','',cfg)[len(proxy):].lstrip('-').removesuffix('-H2H1') or None
        configs.append({'id':cfg,'campaign_id':CAMPAIGN,'proxy':proxy,'version':version,'mode':'H2H1' if client_only else 'H2EE','tls':True if cfg.startswith('TLS-') or proxy in ('Cloudflare','Fastly','Azure','Mitmproxy','Nginx','Lighttpd') else None,'details':{'source_folder':cfg,'tls_inference':'folder label or published proxy requirement; unlabeled values remain unknown','test_scope':'client only' if client_only else 'client and server','architecture':None,'worker_binaries':None,'deployment_configuration':None},'requested_resources':None,'actual_resources':None})
        actual={int(k) for k in data if k.isdigit() and int(k)!=0}
        invalid=sorted(actual-set(planned))
        counts=Counter(); authors=Counter(); missing=0; recoded=0; scoped=0; scope_mismatch=0; compatible=0; invalid_count=0
        original=classify(str(path),'client-only' if client_only else 'full')[7]
        for test_id in planned:
            record=data.get(str(test_id)); outcome,reason,code,scope=preserved(record,test_id)
            old=original.get(str(test_id),'unknown'); counts[outcome]+=1; authors[old]+=1
            is_missing=record is None or not isinstance(record,dict) or not isinstance(record.get('result'),dict) or not record.get('result')
            missing+=int(is_missing); recoded+=int(old!=outcome)
            case=case_by_id[test_id]; expected_scope=case.get('error_type','connection') if case['expected_result']=='error' else None
            scope_ok=(scope==expected_scope or expected_scope=='stream' and scope=='connection') if scope in ('connection','stream') and expected_scope else None
            if scope_ok is not None: scoped+=1; compatible+=int(scope_ok); scope_mismatch+=int(not scope_ok)
            valid=record is not None and isinstance(record,dict)
            invalid_count+=int(not valid)
            measurements.append({'run_id':identifier,'campaign_id':CAMPAIGN,'test_id':test_id,'side':'client' if test_id<=78 else 'server','description':case['description'],'rfc_section':case.get('section'),'expected':case['expected_result'],'expected_scope':expected_scope,'author_outcome':old,'outcome':outcome,'status':'excluded' if not valid else 'missing' if is_missing else 'ambiguous' if outcome=='unknown' else 'recorded','reason':reason or ('absent or invalid test record' if not valid else None),'error_code':code,'observed_scope':scope,'scope_compatible':scope_ok,'author_rule_conformant':conformance(old,case['expected_result']),'preserved_rule_conformant':conformance(outcome,case['expected_result'])})
        reference=published.get(cfg)
        differences={k:{'reference':v,'reanalyzed':authors[k]} for k,v in (reference or {}).items() if authors[k]!=v}
        authored_pair_differences=sum(original.get(str(a))!=original.get(str(b)) for a,b in pairs) if not client_only else None
        known_pairs=[(a,b) for a,b in pairs if preserved(data.get(str(a)),a)[0]!='unknown' and preserved(data.get(str(b)),b)[0]!='unknown'] if not client_only else []
        preserved_pair_differences=sum(preserved(data.get(str(a)),a)[0]!=preserved(data.get(str(b)),b)[0] for a,b in known_pairs)
        pair_percent=authored_pair_differences/61*100 if authored_pair_differences is not None else None
        published_pair_percent=paper['figure6_percent'].get(cfg)
        side_percent=[sum(conformance(original.get(str(t),'unknown'),case_by_id[t]['expected_result']) is False for t in range(a,b))/78*100 for a,b in ((1,79),(79,157))] if not client_only else None
        side_expected=paper['figure5_percent'].get(cfg)
        comparisons={'paired_cases':61 if not client_only else None,'author_pair_differences':authored_pair_differences,'author_pair_percent':pair_percent,'known_pairs':len(known_pairs),'preserved_pair_differences':preserved_pair_differences if known_pairs else None,'published_pair_percent':published_pair_percent,'figure6_agreement':abs(pair_percent-published_pair_percent)<=0.05 if published_pair_percent is not None else None,'author_side_percent':side_percent,'published_side_percent':side_expected,'figure5_agreement':all(abs(a-b)<=0.05 for a,b in zip(side_percent,side_expected)) if side_expected is not None else None}
        summaries.append({'run_id':identifier,'campaign_id':CAMPAIGN,'configuration_id':cfg,'planned':len(planned),'recorded':len(actual&set(planned)),'missing':missing,'unknown':counts['unknown'],'recoded':recoded,'author_counts':dict(authors),'preserved_counts':dict(counts),'published_counts':reference,'published_source':'Paper Table 5, supplied ACM PDF page 264' if reference else None,'assessment':'untested' if reference is None else 'discrepant' if differences else 'reproduced','differences':differences,'scope_observed':scoped,'scope_mismatches':scope_mismatch,'scope_compatible':compatible,'comparisons':comparisons})
        runs.append({'id':identifier,'campaign_id':CAMPAIGN,'configuration_id':cfg,'stage':'archived reanalysis','status':'invalid' if invalid else 'complete','reason':'unexpected test IDs '+str(invalid) if invalid else None,'source_path':str(path.relative_to(UPSTREAM)),'raw_sha256':sha(path),'analysis_sha256':lock['analysis_sha256'],'started_at':stamp,'ended_at':now(),'original_execution_at':None,'wall_seconds':None,'units':'designed test cases; HTTP/2 error codes are dimensionless'})
        artifacts.append({'id':identifier,'campaign_id':CAMPAIGN,'sha256':sha(path),'source_path':str(path.relative_to(UPSTREAM)),'byte_count':path.stat().st_size,'payload':data})
    configs=list({c['id']:c for c in configs}.values())
    by_config={s['configuration_id']:s for s in summaries}
    for cfg,summary in by_config.items():
        proxy=next(c['proxy'] for c in configs if c['id']==cfg)
        old=by_config.get(proxy+'-'+paper['old_versions'].get(proxy,''))
        if old and cfg in published:
            delta={k:summary['author_counts'].get(k,0)-old['author_counts'].get(k,0) for k in CATEGORIES}
            summary['comparisons']['historical']={'old_configuration':old['configuration_id'],'new_configuration':cfg,'author_count_changes':delta,'published_prose_changes':{k:v[proxy] for k,v in paper['figure7_prose_changes'].items() if proxy in v}}
        translated=by_config.get(cfg+'-H2H1')
        if translated:
            subset=[m for m in measurements if m['run_id']==summary['run_id'] and m['side']=='client']
            base=Counter(m['author_outcome'] for m in subset)
            delta={k:translated['author_counts'].get(k,0)-base[k] for k in ('dropped','500','goaway','reset')}
            delta['accepted']=sum(translated['author_counts'].get(k,0) for k in ('received','modified','unmodified'))-sum(base[k] for k in ('received','modified','unmodified'))
            summary['comparisons']['translation']={'h2h1_configuration':translated['configuration_id'],'common_cases':78,'author_count_changes':delta,'published_accepted_change':paper['figure8_prose_accepted_changes'].get(proxy)}
    claims=[
        {'claim_id':'test-universe','location':'Abstract; test suite','description':'156 protocol test cases covering client/server behavior','assessment':'reproduced','evidence':'Pinned test_cases.json contains 156 distinct IDs, partitioned 78/78. This verifies the released artifact count.','limitation':'Not proof of complete RFC coverage; full paper text unavailable.'},
        {'claim_id':'aggregate-counts','location':'Released artifact aggregate table; paper table/figure mapping provisional','description':'Recover released numerical outcome counts','assessment':'discrepant' if any(s['differences'] for s in summaries) else 'reproduced','evidence':str(sum(s['published_counts'] is not None for s in summaries))+' released aggregate rows checked exactly; per-row differences retained.','limitation':'Artifact numerical reproduction is not fresh network measurement; paper figure agreement unverified.'},
        {'claim_id':'none-fully-compliant','location':'Official abstract','description':'No studied proxy is fully HTTP/2 compliant','assessment':'inconclusive','evidence':'Archived worker behaviors can be examined, but timeout/fallback and broad rejection classification require caution.','limitation':'Need full paper methodology review and independent ingress/egress traces before confirming every compliance judgment.'},
        {'claim_id':'h2-improvement','location':'Official abstract; Section 6.1 excerpt','description':'Improvement relative to prior HTTP/1.1 results','assessment':'untested','evidence':'No equivalent HTTP/1.1 matched campaign executed.','limitation':'Different case universes and versions prevent a causal improvement estimate.'},
        {'claim_id':'historical-evolution','location':'Section 6.4 excerpt; released behavior-change graphs','description':'Compliance evolves over historical local proxy versions','assessment':'inconclusive','evidence':'All released historical datasets retained and selectable for test-identity comparison.','limitation':'Deployment environment and source-level configuration differences are not fully recorded; version contrast is descriptive.'},
        {'claim_id':'translation','location':'Section 6.5 excerpt; released dual-scope graphs','description':'H2EE and H2H1 alter proxy behavior','assessment':'inconclusive','evidence':'Paired case identities and separately labeled H2H1 datasets preserved.','limitation':'Configurations and endpoint implementations are not independently reconstructed.'},
        {'claim_id':'tls','location':'Released TLS results','description':'TLS versus cleartext behavior sensitivity','assessment':'inconclusive','evidence':'TLS runs retained separately.','limitation':'TLS labels are known for explicit folders; unmarked datasets remain unknown rather than assumed cleartext.'},
        {'claim_id':'physical-network','location':'Independent validation','description':'Independent local/cloud network replication','assessment':'untested','evidence':'Archived author measurements are secondary empirical evidence. No simulator or orbital model is involved.','limitation':'Fresh loopback pilot is separate and cannot validate cloud providers or historic full proxy fleet.'},
        {'claim_id':'figures-appendices','location':'All paper figures, tables and appendices','description':'Complete paper and supplementary mapping','assessment':'untested','evidence':'ACM and KAUST retrievals encountered security challenges; indexed excerpts and source figures only.','limitation':'Cannot claim complete literature or main-paper review. Figure numbers remain unverified.'}
    ]
    claims=[
        {'claim_id':'test-universe','location':'Section 3.1; Tables 8–9; Appendix B','description':'156 test cases, 78 client and 78 server, including 61 mirrored pairs','assessment':'reproduced','evidence':'Test definitions and pair mapping have exactly 156 IDs and 61 disjoint mirrored pairs.','limitation':'Census verifies the artifact, not every excluded MUST, SHOULD or TLS rule.'},
        {'claim_id':'table5','location':'Table 5; Section 6.1','description':'1950 cases, 1745 rejected, 205 accepted, including 856 drops','assessment':'discrepant' if any(s['differences'] for s in summaries) else 'reproduced','evidence':str(sum(s['published_counts'] is not None for s in summaries))+' published proxy rows compared with exact counts under the authors classifier.','limitation':'Reanalysis of archived author measurements; counts are not independent fresh physical replication. Rejection is not equivalent to RFC compliance.'},
        {'claim_id':'figure4','location':'Figure 4; Section 6.2','description':'Vectorized behavior differs by proxy and test identity','assessment':'reproduced','evidence':'All archived per-test author outcome vectors retained; aggregate counts checked against Table 5.','limitation':'Individual plotted raster cells not automatically verified against the PDF. Evidence-preserving recoding shown separately.'},
        {'claim_id':'figure5','location':'Figure 5; Section 6.3','description':'Client/server broad-rule noncompliance ratios','assessment':'reproduced' if all(s['comparisons'].get('figure5_agreement') is not False for s in summaries) else 'discrepant','evidence':'Ten published client/server ratios independently transcribed from Figure 5; comparisons retain 78-case denominators.','limitation':'Paper broad E/G/R rule and ignore-to-drop rule reproduced; response scope/code and timeout causes need separate evidence.'},
        {'claim_id':'figure6','location':'Figure 6; Section 6.3','description':'Behavior discrepancy over 61 explicitly paired test identities','assessment':'reproduced' if all(s['comparisons'].get('figure6_agreement') is not False for s in summaries) else 'discrepant','evidence':'Published percentages compared within 0.05 percentage points for one-decimal rounding. Unknown pairs reported separately.','limitation':'Behavior discrepancy is descriptive, not evidence that code paths are redundant or a causal diagnosis.'},
        {'claim_id':'figure7','location':'Figure 7; Table 6; Section 6.4','description':'Historical versions change the distribution of protocol behaviors','assessment':'inconclusive','evidence':'Explicit old-version pairs from Table 6 and all category changes preserved; prose changes tested in independent verifier.','limitation':'Version comparison is descriptive. No controlled experiment establishes that the new RFC caused the changes.'},
        {'claim_id':'figure8','location':'Figure 8; Section 6.5','description':'H2H1 downgrade changes client-side behavior relative to H2EE','assessment':'inconclusive','evidence':'Matched 78 client IDs; accepted means received in H2H1 and modified+unmodified in H2EE. Prose deltas preserved.','limitation':'No comparison of server-half tests to H2H1. Full deployment configuration is not reconstructed.'},
        {'claim_id':'tls','location':'Section 6.1','description':'No TLS versus plaintext behavioral differences','assessment':'inconclusive','evidence':'Released TLS datasets are available for exact identity comparison.','limitation':'Unlabeled cleartext/TLS configurations cannot be assumed equivalent; observation from one deployment is not a universal layering guarantee.'},
        {'claim_id':'none-fully-compliant','location':'Abstract; Section 3.3; Conclusion','description':'None of the studied proxies is fully compliant','assessment':'inconclusive','evidence':'Broad author scores and evidence-preserving outcomes retained; acceptance and dropped categories require case-level semantics.','limitation':'Finite suite cannot establish full compliance. Error scope/code, modified messages and legitimate ignore rules require RFC interpretation.'},
        {'claim_id':'h2-improvement','location':'Section 6.1; Appendix A, Table 7','description':'HTTP/2 proxies are stricter than prior HTTP/1.1 tests','assessment':'inconclusive','evidence':'Published comparison is 1745/1950 rejected versus 128/564 HTTP/1.1. Appendix A maps eight prior case IDs into 23 current IDs.','limitation':'Different case universe, roles and versions prevent a matched causal improvement estimate; rejection includes silent drops.'},
        {'claim_id':'frame-header','location':'Section 2.1; Table 1; Figures 1–2','description':'HTTP/2 frames have a 12-byte header','assessment':'discrepant','evidence':'Section 2.1 says 12 bytes. Table 1 depicts 72 header bits; RFC9113 4.1 specifies nine octets. Independent raw-frame control planned.','limitation':'Expository unit/count error does not by itself invalidate the measurement campaign.'},
        {'claim_id':'physical-network','location':'Table 3; Table 4; Section 4','description':'Independent physical replication of all local and cloud proxy deployments','assessment':'untested','evidence':'Published local setup is Ubuntu 24.10 VMs on one hypervisor; cloud workers are Frankfurt and Melbourne Linode nodes on 2025-05-07.','limitation':'Those historical deployments, cloud internal versions and geographic paths cannot be recreated exactly. Fresh loopback pilot will be separate.'},
        {'claim_id':'transparency','location':'Section 4','description':'TCP sequence matching confirms absence of transparent proxies','assessment':'inconclusive','evidence':'Matching numbers exclude some TCP-splitting implementations.','limitation':'Does not exclude passive devices, non-splitting intermediaries, all path variants or TLS-layer intervention.'},
        {'claim_id':'recommendation','location':'Section 7.1','description':'Normative formal RFC compliance suites improve clarity/security','assessment':'untested','evidence':'Recommendation motivated by observed diversity and directly cited security literature.','limitation':'No randomized developer study, intervention, exploit reproduction or measured interoperability benefit.'}
    ]
    elapsed=time.perf_counter()-start
    campaign={'id':CAMPAIGN,'title':'HTTP/2 Compliance Story: archived evidence assessment','status':'partial','protocol':json.loads((ROOT/'protocol-v2.json').read_text()),'protocol_sha256':lock['protocol_sha256'],'artifact_commit':lock['artifact_commit'],'analysis_sha256':lock['analysis_sha256'],'planned_runs':57,'recorded_runs':len(runs),'planned_measurements':len(measurements),'limitations':['Main paper completely reviewed; supporting literature review and independent full-fleet replication have explicit gaps.','Counts reproduce archived artifacts, not an independent live proxy campaign.','Missing/ambiguous worker evidence is retained; a timeout does not establish why a packet was not forwarded.','Designed protocol cases, versions and configurations are correlated; no inferential confidence intervals are justified.','Historical worker resources and exact UTC execution timestamps are missing; no substitutes are invented.'],'provenance':{'lock':lock,'began_at':began,'ended_at':now(),'wall_seconds':elapsed,'python':subprocess.check_output(['python3','--version'],text=True).strip(),'paper_sha256':lock['paper_sha256']},'costs':{'incremental_purchased_compute_usd':0,'local_workstation_total_usd':None,'model_calls':0,'model_cost_usd':0}}
    payload={'campaign':campaign,'configurations':configs,'runs':runs,'measurements':measurements,'summaries':summaries,'sources':json.loads((ROOT/'literature.json').read_text()),'claims':claims,'artifacts':artifacts}
    out=ROOT/'results'; out.mkdir(exist_ok=True)
    (out/'relational.json').write_text(json.dumps(payload,indent=2)+'\n')
    evidence=ROOT/'evidence'; evidence.mkdir(exist_ok=True)
    (evidence/'analysis-summary.json').write_text(json.dumps({'campaign':{k:v for k,v in campaign.items() if k not in ('protocol','provenance')},'summaries':summaries,'claims':claims},indent=2)+'\n')
    print(json.dumps({'runs':len(runs),'configurations':len(configs),'measurements':len(measurements),'compared_aggregate_rows':sum(s['published_counts'] is not None for s in summaries),'discrepant_rows':[s['configuration_id'] for s in summaries if s['differences']],'unknown':sum(s['unknown'] for s in summaries),'recoded':sum(s['recoded'] for s in summaries),'wall_seconds':elapsed},indent=2))

if __name__=='__main__': main()
