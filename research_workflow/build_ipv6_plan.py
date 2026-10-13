"""Build the registered IPv6 plan from the retained assessment, without reruns.

This is a versioned transformation of existing source/claim and summary records.
It does not supply new measurements or independently validate the old pipeline.
"""
import json
import uuid
from pathlib import Path
import bridge
import preparation
from workflow import canonical, digest, numerical_protocol, validate_protocol

BASE = 'c9b2a729-f9bf-46e1-9740-06f0fa499d34'
ROOT = Path(__file__).resolve().parents[1]
ASSESSMENT_SHA256 = '3c3532901aa4b507a61849666d925a0268c0658c0129dbfd729514df8c3a07b0'

def build(path):
    original_bytes = path.read_bytes(); original = json.loads(original_bytes)
    source_sha = digest(original_bytes)
    if source_sha != ASSESSMENT_SHA256: raise ValueError('Assessment bytes differ from the pinned v1 source; preserve them and define a new plan version.')
    imported = bridge.ipv6(path, BASE)
    records = imported['records']; by_claim = {c['scope']['origin_claim_id']: c['id'] for c in (e['record'] for e in records if e['kind'] == 'claims')}
    source_id = next(e['record']['id'] for e in records if e['kind'] == 'sources' and e['record']['id'] == str(uuid.uuid5(uuid.UUID(BASE), 'source:0')))
    config_index = {c['id']: c for c in original['configurations']}
    configurations = []
    for config in original['configurations']:
        details = {key: value for key, value in config.items() if key not in ('id', 'requested_resources', 'actual_resources')}
        details['data_origin'] = 'archived-data'
        row = dict(id=str(uuid.uuid5(uuid.UUID(BASE), 'configuration:' + config['id'])), identity=config['id'], details=details,
          input_versions=[dict(identity='ipv6-dns-assessment-v2.json', sha256=source_sha)], requested_resources=dict(new_network_hosts=0, model_calls=0))
        records.append(dict(kind='configurations', record=row)); configurations.append(row)
    comparisons = {c['published_id']: c for c in original['comparisons']}
    campaigns = []
    for figure in ('13a', '13b', '14a', '14b', '15a', '15b'):
        selected = [p for p in original['published'] if p['id'].startswith('fig' + figure + '-')]
        if len(selected) != 192: raise ValueError('Registered plan must retain all 192 cells in each reviewed absolute panel.')
        published, observed, selected_configs = [], [], set()
        for value in selected:
            comparison = comparisons[value['id']]
            identity = value['epoch'] + ':' + value['configuration_id']; selected_configs.add(identity)
            row = dict(observation_id=value['id'], configuration_identity=identity, metric=value['metric'], units=value['units'], value=value['value'], status='recorded', reason=None)
            published.append(dict(row, source_id=source_id, location=value['location'], extraction=value['extraction']))
            observed.append(dict(row, value=comparison['reproduced']))
        raw = canonical(dict(published=published, observed=observed))
        configs = [dict(identity=identity, details={**{key: config_index[identity.split(':', 1)[1]][key] for key in ('cohort', 'mtu', 'upstream', 'family', 'edns_bytes')}, 'epoch': identity.split(':', 1)[0], 'configuration_identity': identity.split(':', 1)[1], 'observation_origin': 'Retained archived mean; full source configuration and cohort definition are in the imported configuration record.'}) for identity in sorted(selected_configs)]
        document = numerical_protocol(digest(raw), 'TIMEOUT', 'percent', 0.0051, configs, max_observations=192)
        document['questions'] = ['Do the retained archived-summary values for Figure ' + figure + ' still match its separately transcribed published cells within 0.0051 percentage points?']
        document['metrics'] = [dict(name=metric, units='percent', tolerance_absolute=0.0051) for metric in sorted({p['metric'] for p in selected})]
        document['data_origin'] = 'archived-data'; document['resource_limits']['max_input_bytes'] = 256000
        document['prior_exposure'] = 'All retained values and the original numerical agreement were inspected in the earlier assessment. This is an exposed-data numerical recheck, not held-out validation or new raw-data reanalysis.'
        document['coverage_limits'] = 'Only the 192 reviewed absolute panel cells and retained archived means. Original per-query/packet parsing, relative figures, causality, generalization and current Internet conditions remain uncovered. TCP fallback is a TCP/UDP ratio; other packet metrics use observed UDP server identities, while TIMEOUT/SERVFAIL use NS-set outcomes.'
        document['statistics'] = 'Exact Decimal absolute differences of the supplied percentages; differences are percentage points. No inferential intervals or independent replication inferred.'
        document['input_versions'] += [dict(identity='prior-assessment-v2.json; source of recorded means', sha256=source_sha), dict(identity='reviewed PuRe main.pdf', sha256=original['sources'][0]['sha256'])]
        validate_protocol(document)
        campaigns.append(dict(key='fig-' + figure, campaign=dict(followup_of=None, title='Archived numerical recheck: Figure ' + figure, stage='retrospective-import', experiment_type='reanalysis', adapter='matched-numeric-v1', planned_units=192, coverage_limits=document['coverage_limits']), protocol=document, claim_ids=[by_claim['folded-cells']], execution_mode='automatic', raw_input=raw.decode(), dependencies=[], priority=3, rationale='Recheck retained published and archived-summary values. This execution adds no new network measurements and does not independently validate upstream code or automatically assess a claim.', exclusive_resources=[]))
    # These jobs prepare evidence and experimental designs. They do not pretend
    # that unavailable domain experiments are executable numerical comparisons.
    for claim in original['claims']:
        if claim['id'] == 'folded-cells': continue
        document = dict(questions=['What exact inputs, author revisions and domain protocol are needed to carry out this proposed check: ' + claim['proposed_check']],
          hypotheses='This is an evidence-availability and follow-up planning review. No empirical hypothesis is tested or declared resolved by creating this task.',
          metrics=[dict(name='evidence_readiness', units='qualitative dependency status')], configurations=[dict(identity='followup-' + claim['id'], details=dict(paper_location=claim['location'], claim_type=bridge.TYPE_BY_ID[claim['id']], proposed_check=claim['proposed_check'], original_feasibility=claim['feasibility']))],
          input_versions=[dict(identity='prior-assessment-v2.json; claim and dependency record', sha256=source_sha)], dates_or_epochs='Preserve the paper’s original 2025 measurement epochs; current measurements require a separately declared campaign.',
          algorithms=['Source-grounded operator review of exact dependency availability; no automatic domain experiment.'], seeds=dict(applicable=False, rationale='Qualitative preparation review has no stochastic algorithm.'),
          controls=['Compare source versions and original observation identities; distinguish unavailable dependencies from recorded zero.', 'Retain the earlier finding and its tested conditions; this planning job never upgrades that finding.'], sensitivity=dict(applicable=False, rationale='Empirical sensitivity belongs in the later domain-specific protocol; no experiment executes in this planning task.'),
          replication_unit='One sourced claim and its required evidence; not a statistical replication.', sample_size_rationale='Census of the one claim’s declared required dependencies.', exclusions='Do not substitute a different Internet epoch, aggregate for packet-level inputs, or unrelated implementations.',
          missing_data='Unavailable or ambiguous inputs remain explicitly unavailable or ambiguous. No fabricated data or zero filling.', statistics='Qualitative source and dependency audit; inferential intervals are inapplicable.', dependence='Shares the earlier assessment and reviewer provenance; no independent evidence asserted.',
          resource_limits=dict(max_observations=1, max_input_bytes=1500000, wall_seconds=300, estimated_cost_usd=None, cost_basis='Preparation review budget only; downstream experiment costs are unestimated and need a separate resource declaration.'),
          stopping_rule='Stop preparation if exact evidence is unavailable; retain the gap. A later main experiment requires its own frozen domain protocol and authorized resources.', prior_exposure='Earlier results, limitations and proposed checks have already been examined. This preparation review is not held-out validation.',
          coverage_limits='Planning/source-availability task only. Required domain inputs: ' + claim['required_inputs'] + '. Earlier limitation: ' + claim['limitation'], data_origin='archived-data')
        validate_protocol(document)
        campaigns.append(dict(key='plan-' + claim['id'], campaign=dict(followup_of=None, title='Prepare follow-up: ' + claim['description'], stage='follow-up', experiment_type='source-review', adapter='domain-operator', planned_units=1, coverage_limits=document['coverage_limits']), protocol=document, claim_ids=[by_claim[claim['id']]], execution_mode='manual', raw_input=None, dependencies=[], priority=4, rationale='Requires operator preparation and the exact dependencies in the frozen planning protocol. Creating this task does not execute or validate the proposed experiment.', exclusive_resources=[]))
    return dict(schema_version=1, plan_id='ipv6-archive-v1', title='IPv6 archived checks and domain follow-up preparation',
      paper_urls=['https://pure.mpg.de/pubman/item/item_3670144_1', 'https://pure.mpg.de/pubman/item/item_3670144_1/component/file_3670145/main.pdf', 'https://pure.mpg.de/rest/items/item_3670144_1/component/file_3670145/content', 'https://doi.org/10.1145/3730567.3764439'],
      provenance=dict(producer='Codex AI implementer; not independent or human review', source_version='ipv6_dns/evidence/assessment-v2.json', source_sha256=source_sha, paper_sha256=next(s['sha256'] for s in original['sources'] if s['reference_number'] == 0), prior_exposure='Earlier full assessment and exposed retained results. Sources/claims/findings are imported unchanged; new jobs recheck archived means or prepare domain follow-ups.', limitations='No new Internet measurements, raw-pcap replay, complete literature review, causal intervention, independent review or automatic claim upgrade. Manual planning tasks are not executed domain experiments.'), records=records, campaigns=campaigns)

if __name__ == '__main__':
    path = ROOT / 'experiments/ipv6_dns/evidence/assessment-v2.json'
    destination = Path(__file__).parent / 'plans/ipv6-archive-v1.json'
    if destination.exists(): raise SystemExit('Preserve the registered plan; use a new version for amendments.')
    plan = build(path); raw = canonical(plan)
    preparation.parse(raw)
    destination.parent.mkdir(parents=True, exist_ok=True); destination.write_bytes(raw + b'\n')
    print(json.dumps(dict(plan_id=plan['plan_id'], original_assessment_sha256=plan['provenance']['source_sha256'], plan_sha256=digest(raw + b'\n'), bytes=len(raw)+1, campaigns=len(plan['campaigns']), automated=6, manual=14, scientific_labels_changed=False)))
