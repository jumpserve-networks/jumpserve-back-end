"""Validate the declared schedule and emit relational records and evidence."""
import hashlib
import argparse
import json
import math
import platform
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent
COUNTRIES = ['tonga','haiti','lithuania','ghana','britain','southafrica']
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--saturation',action='store_true'); args=parser.parse_args()
    protocol_path=ROOT/('saturation-protocol.json' if args.saturation else 'protocol.json')
    folder=ROOT/('results/saturation' if args.saturation else 'results')
    protocol = json.loads(protocol_path.read_text())
    rows = [json.loads(line) for line in (folder/'samples.jsonl').read_text().splitlines()]
    schedule = json.loads((folder/'schedule.json').read_text())
    expected = {'-'.join(map(str, job)) for job in schedule}
    assert len({r['id'] for r in rows}) == len(rows) == len(expected)
    assert {r['id'] for r in rows} == expected, 'Incomplete declared schedule'
    assert {r['protocol_sha256'] for r in rows} == {sha(protocol_path)}
    assert {r['runner_sha256'] for r in rows} == {sha(ROOT/'run.py')}
    for r in rows:
        assert not r['validation_errors'], r['id']
        assert math.isfinite(r['capacity_gbps']) and 0 <= r['capacity_gbps'] <= min(r['rf_demand_gbps'],r['cell_bound_gbps'],r['deployed_terminals']*.1)+1e-6
    summaries=[]
    for country in COUNTRIES:
        primary=[r for r in rows if r['country']==country and r['constellation']=='starlink_5shells' and r['requested_terminals']==(2000000 if args.saturation else 200000) and r['placement']=='gcb-0' and r['beam_policy']=='greedy-coordinated' and r['ku_gbps']==1.28 and r['variant']=='paper' and r['second']<15]
        assert {r['second'] for r in primary} == set(range(15)), country
        values=[r['capacity_gbps'] for r in primary]
        mean=statistics.mean(values); published=primary[0]['published_capacity_gbps']; delta=100*(mean/published-1)
        summaries.append(dict(country=country,published_gbps=published,mean_gbps=mean,min_gbps=min(values),max_gbps=max(values),relative_difference_percent=delta,failover_percent=100*mean/primary[0]['lost_capacity_gbps'],snapshots=15,assessment='within 5%' if abs(delta)<=5 else 'outside 5%'))
    configuration_fields=['country','constellation','satellites','requested_terminals','deployed_terminals','placement','beam_policy','ku_gbps','variant','cells','lost_capacity_gbps','published_capacity_gbps']
    sample_fields=['id','second','capacity_gbps','rf_demand_gbps','cell_bound_gbps','failover_percent','served_cells','allocated_beams','validation_errors','graph_sha256','demands_sha256','terminal_sha256','runner_sha256','wall_seconds']
    configurations={}; samples=[]
    for r in rows:
        cid=r['id'].rsplit('-',1)[0]
        config=dict(id=cid,**{k:r[k] for k in configuration_fields})
        assert cid not in configurations or configurations[cid]==config
        configurations[cid]=config
        samples.append(dict(configuration_id=cid,**{k:r[k] for k in sample_fields}))
    papers=json.loads((ROOT/'literature.json').read_text())
    main_paper=dict(reference_number=0,citation='Bhosale et al. (2025). Assessing LEO Satellite Networks for National Emergency Failover. DOI: 10.1145/3730567.3764482',kind='main paper',source_url='https://saeed.github.io/files/cosmosim-imc25.pdf',download_status='downloaded',reading_status='reviewed',pages=20,sha256=sha(ROOT/'sources/main-paper.pdf'),reading_notes='Full twenty-page paper, Algorithms 1–2, evaluation, bibliography, appendices and figures reviewed. Findings are optimistic simulation bounds conditioned on coordinated deployment, working gateways and no competing traffic. No physical nationwide failover was measured. Table 1 prints Lithuania capacity 2,005 Gbps, lost cable capacity 101 Gbps and 198 percent; the two capacities instead imply 1,985.15 percent. We preserve the published capacity values and flag the inconsistent percentage without inferring which printed field is wrong.')
    paper_keys=['reference_number','citation','kind','source_url','download_status','reading_status','pages','sha256','version_note','reading_notes']
    papers=[dict(**{k:p.get(k) for k in paper_keys},retrieval_audit={k:p[k] for k in ['attempts','doi_candidate','metadata_title','metadata_error','metadata_pdf_candidates','text_characters'] if k in p}) for p in [main_paper]+papers]
    claims=[
      ('capacity','Table 1','Six-country maximum downlink and lost-cable fraction','15 primary states per country; signed numerical agreement','Model and time-window agreement only. Uncertainty in inputs is not estimated.'),
      ('placement','Figures 5, 13','Terminal budget, population placement and GCB eligibility caps','Six-country curves and matched capped/uncapped ablations','Uses corrected exact-budget allocation; artifact semantics reported separately.'),
      ('beams','Figure 9','Coordinated versus uncoordinated beam allocation','Matched t=0 contrasts for both allocation variants','Released heuristic reused. No proof of an optimal RF solution.'),
      ('growth','Figure 12','Larger future constellations','14,316 and 34,224 satellite uniform-placement sensitivities','Single orbital epoch; not a claim of operational availability.'),
      ('rates','Figures 15–16','Changes to link capacities','0.956 and 2.5 Gbps Ku sensitivities','2.5 case automatically doubles ISLs in artifact, keeps Ka unchanged and baseline constellation. Full Config C not reproduced.'),
      ('routing','Figures 6–8, 10, 22','Hot-potato routing and ISL utilization','Not reproduced','Maximum flow capacity only. Cost-minimizing routes and utilization are not retained.'),
      ('sovereignty','Figure 11','Domestic versus foreign gateway dependence','Not reproduced','Gateway sovereignty masks are absent from this campaign.'),
      ('incumbents','Figures 14, 23–24','Existing-user competition','Not reproduced','Primary no-incumbent upper bound must not be presented as capacity available under ordinary load.'),
      ('measurement','Figures 1, 18–19, 21','Cable ranking and outage measurement validation','Not independently reproduced','Published cable values and released cell/gateway data reused. No RIPE Atlas/Calypso campaign or physical satellite measurement.'),
    ]
    limitations=protocol['limitations']+[
      'Post-execution clarification: released code doubles ISL rates when Ku=2.5 Gbps. The original frozen protocol described this as Ku-only; the amended interpretation is a Ku+ISL sensitivity, with baseline Ka and constellation. The frozen document remains unchanged for audit.',
      'Literature review is recorded source by source; inaccessible or unread sources are not treated as reviewed.',
      'Primary execution preceded completion of the supporting bibliography review; later review does not retrospectively alter its predeclared configurations.',
      'Published Table 1 arithmetic inconsistency: Lithuania prints 2,005 Gbps satellite capacity, 101 Gbps lost cable capacity and 198 percent. Dividing the printed capacities gives 1,985.15 percent. Recorded capacity/cable fractions use the printed capacity denominator; no source field is silently corrected.',
    ]
    provenance={'execution':'local CPU simulation, no AWS satellite test infrastructure','python':platform.python_version(),'runner_sha256':sha(ROOT/'run.py'),'analysis_sha256':sha(Path(__file__)),'samples_sha256':sha(folder/'samples.jsonl'),'schedule_sha256':sha(folder/'schedule.json'),'literature_sha256':sha(ROOT/'literature.json'),'graph_generation':'Exact PyEphem distances after conservative spatial pruning; two exhaustive cell audits per country/epoch','source_files':{str(p.relative_to(ROOT/'sources/CosmoSim')):sha(p) for p in sorted((ROOT/'sources/CosmoSim/inputs').rglob('*')) if p.is_file()},'protocol_clarification':'Ku=2.5 invokes doubled ISLs, unchanged Ka; original protocol retained.'}
    provenance['constellation_files']={str(p.relative_to(ROOT/'sources/CosmoSim')):sha(p) for p in sorted((ROOT/'sources/CosmoSim/constellation_configurations').rglob('*')) if p.is_file() and p.suffix in {'.txt','.yaml'}}
    provenance['constellation_epochs']={name:json.loads((ROOT/f'results/raw/{name}/tonga/graph_0.json').read_text())['epoch'] for name in sorted({r['constellation'] for r in rows})}
    provenance['epoch_interpretation']='Generated idealized TLE epoch, not the wall-clock date of execution or a measured operational constellation.'
    if args.saturation:
        provenance['budget_convergence']={country:[{'terminals':r['requested_terminals'],'gbps':r['capacity_gbps']} for r in rows if r['country']==country and r['second']==0 and r['variant']=='paper'] for country in COUNTRIES}
        claims=[('saturation','Table 1','Terminal-budget saturation follow-up','Four tested budgets, 15 temporal states at two million, released allocation check at four million','Exploratory follow-up designed after the initial results; a tested plateau is not a proof of global optimality.')]
    campaign=dict(id=protocol['id'],title=protocol['title'],status='complete',protocol=protocol,protocol_sha256=sha(protocol_path),artifact_commit=protocol['artifact_commit'],paper_sha256=main_paper['sha256'],planned_samples=len(schedule),recorded_samples=len(rows),limitations=limitations,provenance=provenance)
    payload=dict(campaign=campaign,configurations=list(configurations.values()),samples=samples,summaries=summaries,papers=papers,claims=[dict(zip(['claim_id','figure','description','coverage','limitation'],c)) for c in claims])
    (folder/'relational.json').write_text(json.dumps(payload,indent=2)+'\n')
    evidence=ROOT/('evidence/saturation' if args.saturation else 'evidence'); evidence.mkdir(exist_ok=True,parents=True)
    (evidence/'summaries.json').write_text(json.dumps(summaries,indent=2)+'\n')
    (evidence/'provenance.json').write_text(json.dumps(dict(campaign=campaign,configurations=len(configurations),samples=len(rows)),indent=2)+'\n')
    print(json.dumps(summaries,indent=2))
if __name__=='__main__': main()
