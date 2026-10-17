"""Lossless references and bounded projections of existing historical assessments.

This adapter transfers evidence; it does not execute historical experiments or
certify contributed scientific findings. Native per-observation tables remain
the source of detailed data. Every imported finding retains its original scope.
"""
from collections import Counter
from decimal import Decimal
import json
from pathlib import Path
import uuid

from workflow import FIELDS, canonical, digest, now, public_url, validate_record
from bridge import TYPE_BY_ID

VERSION = 'archived-assessment-import-v1'
NAMESPACE = uuid.UUID('4caa39b3-e839-4bbb-84cb-cb4b67ce0c4d')
REVIEWER = dict(identity='Codex historical evidence importer', type='AI',
                independence='Same AI as integration implementer; no independent or human review asserted.')
REVIEW_STATES = {'complete': 'complete-review', 'partial': 'partial-review',
                'complete-review': 'complete-review', 'partial-review': 'partial-review',
                'retrieved-unreviewed': 'retrieved-unreviewed', 'unavailable-full-text': 'unavailable-full-text'}

def uid(study, name):
    return str(uuid.uuid5(uuid.UUID(study), name))

def number(value):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError('A Boolean is not a scientific measurement.')
    parsed = Decimal(str(value))
    if not parsed.is_finite():
        raise ValueError('A nonfinite historical value needs an explicit invalid state.')
    if parsed == parsed.to_integral_value():
        return int(parsed)
    converted = float(parsed)
    # Stop rather than silently lose precision; originals also retain exact tokens.
    if Decimal(str(converted)) != parsed:
        raise ValueError('Historical decimal cannot survive the existing numerical schema.')
    return converted

def source_rows(native, source_table, study, root):
    rows = []; seen = set()
    main = None
    definition = next((m['review_definition'] for m in native.get('meta', []) if m.get('review_definition')), None)
    for original in native[source_table]:
        content = {k: v for k, v in original.items() if k != 'campaign_id'}
        identity = digest(canonical(content))
        if identity in seen:
            continue
        seen.add(identity)
        review = original.get('review_status', original.get('reading_status', 'unrecorded'))
        status = REVIEW_STATES.get(review)
        access = original.get('access_status', original.get('download_status', 'unrecorded'))
        if status is None:
            status = ('partial-review' if review in ('reviewed', 'read', 'partial') else
                      'retrieved-unreviewed' if original.get('sha256') else 'unavailable-full-text')
        finding = original.get('findings') or original.get('reading_notes') or 'No substantive review finding recorded in the original register.'
        limitation = original.get('limitations') or original.get('version_note') or 'Original reading scope and retrieval metadata are limited; import is not an independent source review.'
        def safe_url(field):
            value = original.get(field)
            if value is None:
                return None
            try:
                return public_url(value)
            except ValueError:
                # A supplied-file location is not a public HTTP URL. The exact
                # original field remains in the retained private database export.
                return None
        row = dict(id=uid(study, 'source:' + identity), citation=original['citation'],
            source_url=safe_url('source_url'), retrieved_url=safe_url('retrieved_url'),
            kind=original['kind'], role=original.get('role') or 'Main assessed paper' if original['reference_number'] == 0 else original.get('role') or 'Direct cited source; original role classification retained where available.',
            retrieved_version=original.get('retrieved_version') or original.get('version_note') or 'Historical imported version; further version metadata was not recorded.',
            sha256=original.get('sha256'), byte_count=original.get('byte_count'),
            access_status='imported:' + access, review_status=status,
            review_definition=definition or 'Complete review requires all text, figures, tables, appendices and supplements to be examined. An original "reviewed" flag alone is conservatively partial.',
            examined=finding, unexamined='none' if status == 'complete-review' else limitation,
            retrieval_attempts=original.get('retrieval_attempts', []) + [dict(native_review_status=review, native_retrieval_audit=original.get('retrieval_audit'),
                nonpublic_location_fields=[field for field in ('source_url', 'retrieved_url') if original.get(field) is not None and safe_url(field) is None],
                import_note='Historical declaration retained; no new full-text review or retrieval inferred. Non-URL locations remain in the private original export.')],
            reviewer=original.get('reviewer') or dict(REVIEWER, identity='Original AI assessment; identity not further recorded in this native source row'),
            findings=finding, limitations=limitation)
        rows.append(validate_record('sources', row))
        if original['reference_number'] == 0:
            main = row
    if main is None:
        # Delay's old source table has only references 1–60; retain the exact main
        # paper bytes and original reading notes as additional supplied evidence.
        original = root / 'experiments/nines2026_delay_equalization/literature/primary.pdf'
        notes = root / 'experiments/nines2026_delay_equalization/PRIMARY_READING.md'
        raw = original.read_bytes()
        main = dict(id=uid(study, 'main-paper'), citation='Illick, Roger, Misra and Rubenstein. Making Congestion Control Algorithms Insensitive to Underlying Propagation Delays. NINeS 2026, article 27.',
            source_url='https://doi.org/10.4230/OASIcs.NINeS.2026.27', retrieved_url=None,
            kind='main paper', role='Main assessed paper', retrieved_version='Previously reviewed 26-page publisher PDF retained in the delay campaign.',
            sha256=digest(raw), byte_count=len(raw), access_status='imported:local-original', review_status='partial-review',
            review_definition='All text, figures, tables and appendices are required for complete review; the historical figure audit has limits.',
            examined='Original PRIMARY_READING.md reports all 26 text pages read on 2026-09-27 and a separate figure audit.',
            unexamined='Figure inspection and original experimental coverage are not complete; see the retained primary reading audit.',
            retrieval_attempts=[dict(status='Imported retained original PDF; historical HTTP metadata not reconstructed', sha256=digest(raw), reading_notes_sha256=digest(notes.read_bytes()))],
            reviewer=REVIEWER, findings='Delay is round-trip; sensitivity is log2(max/min) across matched assignments. Scope and apparatus differences are preserved.',
            limitations='This transfer did not repeat the original substantive paper review or expand its scientific coverage.')
        rows.insert(0, main)
    return rows, main['id']

def claim_finding(module, original, native):
    if 'assessment' in original:
        return original['assessment'], original.get('evidence') or 'Original assessment register; see native evidence.', 'Original explicit label retained unchanged.'
    key = original['claim_id']
    if module == 'propagation-delay-study':
        labels = dict(forward_latency='reproduced', bbr1_delta='reproduced', bbr3_delta='reproduced', shift='reproduced', narrow='discrepant', gcp='untested', copa='untested', video='untested')
        evidence = dict(forward_latency='The original 24 UDP-control records (24,000 packets) support the ACK-direction mechanism on the tested uncongested virtual path.',
            bbr1_delta='Both identified BBRv1 flows have positive whole-trial paired-bootstrap reduction intervals under the original finite grid.',
            bbr3_delta='Both identified BBRv3 flows have positive whole-trial paired-bootstrap reduction intervals under the original finite grid.',
            shift='All four identified flows have positive pointwise whole-trial paired-bootstrap reduction intervals.',
            narrow='Illinois, CUBIC and Reno intervention sensitivity remains above one under the recorded finite-grid apparatus; only Westwood satisfies the below-one threshold.',
            gcp='No matching GCP Internet-path campaign was executed.', copa='The separate Copa/ns-3 scenario was not assessed.', video='Bulk TCP and UDP controls do not assess video/application QoE.')
        return labels[key], evidence[key], 'Conservative label crosswalk from original RESULTS.md and native summaries; original coverage field retained. Directional reproduction does not assert exact numerical equality with published curves.'
    if module == 'leo-emergency-failover':
        if key == 'capacity':
            return 'discrepant', 'Original 200,000-terminal fixed-window means exceed the ±5% numerical criterion for Britain, Ghana and Haiti; Lithuania also has a printed percentage inconsistency. Published maxima and fixed-window means are reported separately.', 'Derived from original recorded summary criteria, not a new simulation or operational validation.'
        if key in ('routing', 'sovereignty', 'incumbents', 'measurement'):
            return 'untested', original['coverage'], 'Original coverage explicitly excludes the required campaign; no substitution from capacity arithmetic.'
        return 'inconclusive', original['coverage'], 'Relevant ablations/sensitivities are recorded, but coverage alone does not establish full reproduction of every published curve or broader claim.'
    raise ValueError('An explicit historical assessment or documented conservative crosswalk is required.')

def split_ipv6_coverage(summaries):
    rows = []
    for original in summaries:
        groups = {}
        for metric, value in original['statistics'].items():
            coverage = (value['planned'], value['n'], value['missing'], value['invalid'])
            if coverage[0] != sum(coverage[1:]):
                raise ValueError('Original DNS metric coverage does not conserve observations.')
            groups.setdefault(coverage, {})[metric] = value
        for values in groups.values():
            rows.append(dict(original, statistics=values,
                coverage_note='Counts refer to the same valid days for the listed metrics; metrics with different missingness have separate summaries. Not independent samples.'))
    return rows

def build(export_path, definition, root, declared_protocol_sha):
    tick = now(); raw = Path(export_path).read_bytes(); exported = json.loads(raw)
    native = exported['data']; module = exported['module']
    study = str(uuid.uuid5(NAMESPACE, module + ':' + VERSION))
    audit_run = uid(study, 'import-audit-run'); campaign = uid(study, 'import-audit-campaign')
    source, main_source = source_rows(native, definition['source'], study, root)
    records = [dict(kind='sources', record=row) for row in source]
    def add(kind, name, **fields):
        row = validate_record(kind, dict(id=uid(study, kind + ':' + name), **fields))
        records.append(dict(kind=kind, record=row)); return row['id']
    counts = exported['original_relation_counts']
    shared_scope = ('Imported historical assessment completed outside the web application. Claims, direct sources, method declarations, configurations and aggregate/key results are integrated here. Detailed original observations and private originals stay in the linked original module and native relational tables. No historical experiments were rerun by this import.')
    if module == 'ipv6-dns-study':
        shared_scope += ' This is the original IPv6 module assessment; the later owner-workspace follow-ups remain separate and unchanged.'
    paper = dict(title=definition['title'], paper_url='https://doi.org/' + definition['doi'],
                 domain=definition['domain'], scope=shared_scope, origin_module=module)
    doc = dict(questions=['Does the historical assessment transfer preserve source identity, findings, units, missingness, configuration identities and coverage?'],
        hypotheses='Import exact existing evidence and documented conservative labels without representing it as an application-launched experiment.',
        metrics=[dict(name='imported_register_rows', units='records')],
        configurations=[dict(identity='historical-evidence-import', details=dict(module=module))],
        input_versions=[dict(identity=Path(export_path).name, sha256=digest(raw)), dict(identity='archived-assessment-import-protocol-v1.md', sha256=declared_protocol_sha)],
        dates_or_epochs='Original scientific dates/epochs are retained in native_campaigns and native_protocols below; this protocol registers a retrospective transfer.',
        algorithms=['Stable UUID crosswalk and schema/hash/coverage checks; original scientific algorithms remain in their native records.'],
        seeds=dict(applicable=False, rationale='Deterministic historical transfer; original experimental seeds are retained separately.'),
        controls=['Round-trip all stored scientific fields and original export hashes; check native counts unchanged; retain RLS and private artifact restrictions.'],
        sensitivity=dict(applicable=False, rationale='No new scientific experiment; existing sensitivities retained in native method declarations.'),
        replication_unit='Historical campaigns and recorded original units; imported rows and summaries are not independent experimental replications.',
        sample_size_rationale='Census of direct sources/claims and existing configurations/summaries; key numerical projections have explicit coverage. Detailed raw observations remain in native tables.',
        exclusions='No new experiment, model call, queue launch or retrospective fabrication of original timestamps. No raw observation truncation claimed as a full shared export.',
        missing_data='Preserve null, invalid, ambiguous and zero separately; stop on unrepresentable numeric tokens.',
        statistics='No inferential estimates computed by import. Native descriptive ranges and justified whole-trial intervals are retained separately.',
        dependence='Original repeated times, workers, configurations, keys and data sources remain dependent as originally documented.',
        resource_limits=dict(max_observations=5000, max_input_bytes=10_000_000, wall_seconds=300),
        stopping_rule='Stop on target, schema, coverage, hash, ownership or budget mismatch; preserve evidence and resume unchanged IDs.',
        prior_exposure='Previously completed AI-assisted studies; this integration is not held-out scientific validation.',
        coverage_limits=shared_scope, data_origin='archived-data',
        native_campaigns=native['campaigns'], native_protocols=native.get('protocols', []),
        native_relation_counts=counts, native_export_url='https://jumpserve.quaint-lab.org/module/' + module + definition['export'],
        native_miscellaneous={k: native[k] for k in ('controls', 'meta', 'published', 'followups', 'latency_trials') if k in native and not (module == 'ipv6-dns-study' and k == 'published')})
    protocol_id = add('protocols', 'retrospective-wrapper', version=1, supersedes_id=None, document=doc,
         sha256=digest(canonical(doc)), frozen_at=tick, registration_kind='retrospective-import', amendment_reason=None)
    records.append(dict(kind='campaigns', record=validate_record('campaigns', dict(id=campaign,
        protocol_id=protocol_id, followup_of=None, title='Historical evidence import and validation — ' + definition['title'],
        stage='retrospective-import', experiment_type='source-review', adapter=VERSION,
        planned_units=len(native['claims']), coverage_limits=shared_scope))))
    for c in native['configurations']:
        add('configurations', str(c['id']), identity=str(c['id']), details=dict(native_record=c,
            origin='Historical original configuration; transfer does not execute it.'),
            input_versions=[dict(identity=Path(export_path).name, sha256=digest(raw))],
            requested_resources=c.get('requested_resources') or dict(recorded=None, rationale='See original native campaign; no experiment resources requested by import.'))
    for original in native['claims']:
        key = str(original.get('id', original.get('claim_id')))
        identity = original.get('campaign_id', '') + ':' + key
        label, evidence, mapping = claim_finding(module, original, native)
        priority = 3
        claim = add('claims', identity, source_id=main_source, location=original.get('location', original.get('figure')),
            description=original['description'], claim_type=TYPE_BY_ID.get(key, 'theory' if key == 'theory' else 'numerical'),
            scope=dict(origin='completed historical assessment outside the web application', native_claim=original,
                       mapping=mapping, native_export_sha256=digest(raw)), metrics=[], priority=priority)
        assessment = add('assessments', identity, claim_id=claim, campaign_id=campaign, supersedes_id=None,
            label=label, tested_conditions=dict(native_claim_identity=identity, origin_module=module,
            provenance='Imported original finding or conservative documented crosswalk; no new experiment.',
            original_dates='See retained native campaign and protocol declarations.', original_scope=original.get('coverage', original.get('feasibility'))),
            evidence=[dict(native_module=module, native_claim_identity=identity, export_sha256=digest(raw), native_relation=definition['prefix']+'claims')],
            justification=evidence, limitations=original['limitation'] + ' ' + mapping, reviewer=REVIEWER)
        add('claim_checks', identity, claim_id=claim, campaign_id=campaign, method='Historical assessment import; original experimental methods remain in native records.',
            applicability='applicable', rationale='Links imported evidence to its transfer audit; does not imply a new empirical test or certify independent review.')
        if label in ('inconclusive', 'untested'):
            add('gaps', identity, claim_id=claim, assessment_id=assessment, supersedes_id=None, status='open',
                reason=original['limitation'], next_check=original.get('proposed_check') or 'Prepare a domain-specific follow-up for the original claim with exact inputs and comparison criteria.',
                required_inputs=[original.get('required_inputs') or original['limitation']], dependencies=[dict(status='Not resolved by import', original_scope=original.get('coverage'))],
                feasibility=original.get('feasibility') or 'Requires a separately reviewed domain protocol and dependency audit.',
                estimated_cost_usd=None, cost_basis='Not estimated for this follow-up; missing usage is not zero cost.',
                decision_rule='Freeze claim-specific criteria before new execution; original evidence and labels remain preserved.',
                stopping_rule='Stop if exact required inputs, equipment, authorization or budget are unavailable; retain the unresolved claim.', priority=priority, campaign_id=None)
    def published(name, observation, config, metric, units, value, location):
        return add('published_values', name, source_id=main_source, location=location, observation_id=observation,
            configuration_identity=config, metric=metric, units=units, value=number(value),
            status='recorded' if value is not None else 'missing', reason=None if value is not None else 'Not recorded in original source.',
            extraction='Native published value; source and original export retained.')
    def measurement(name, config, metric, units, value, pub=None, **details):
        add('measurements', name, run_id=audit_run, published_id=pub, observation_id=name,
            configuration_identity=config, metric=metric, units=units, value=number(value),
            status='recorded' if value is not None else 'missing', reason=None if value is not None else 'Original aggregate is missing; not a recorded zero.',
            details=details)
    if module == 'ipv6-dns-study':
        by_id = {}
        for p in native['published']:
            by_id[p['id']] = published(p['id'], p['id'], p['configuration_id'], p['metric'], 'percent', p['value'], p['location'])
        for c in native['comparisons']:
            measurement(c['id'], c['configuration_id'], c['metric'], 'percent', c['reproduced'], by_id[c['published_id']],
                published=number(c['published']), difference=number(c['difference_pp']), tolerance_absolute=0.0051,
                label=c['assessment'], valid_days=c['valid_days'], planned_days=c['planned_days'], epoch=c['epoch'])
    elif module == 'http2-compliance-study':
        for s in native['summaries']:
            for metric, value in s['author_counts'].items():
                key=s['configuration_id']+':author:'+metric
                p=s.get('published_counts'); target=p.get(metric) if p is not None else None
                pub=published(key,key,s['configuration_id'],'author '+metric,'cases',target,s.get('published_source') or 'Table 5') if p is not None else None
                measurement(key,s['configuration_id'],'author '+metric,'cases',value,pub,
                    published=number(target), difference=number(value)-number(target) if target is not None else None,
                    label=s['assessment'] if target is not None else 'unmatched historical aggregate', tolerance_absolute=0 if target is not None else None,
                    denominator=s['planned'], classifier='Authors classifier; rejection is not RFC compliance.')
    elif module == 'leo-emergency-failover':
        for s in native['summaries']:
            key=s['campaign_id']+':'+s['country']; config=key
            published(key,key,config,'published maximum downlink capacity','Gbps',s['published_gbps'],'Table 1')
            measurement(key,config,'fixed-window mean capacity','Gbps',s['mean_gbps'],
                label='fixed-window mean; published maximum is a separate metric', native_assessment=s['assessment'],
                minimum=s['min_gbps'], maximum=s['max_gbps'], snapshots=s['snapshots'], interval_kind='descriptive')
    elif module == 'propagation-delay-study':
        for s in native['summaries']:
            key=s['family']+':'+s['cca_group']+':flow'+str(s['flow_index'])
            for metric in ('baseline_delta', 'treatment_delta', 'improvement'):
                low,high={'baseline_delta':('baseline_ci_low','baseline_ci_high'),'treatment_delta':('treatment_ci_low','treatment_ci_high'),'improvement':('improvement_ci_low','improvement_ci_high')}[metric]
                measurement(key+':'+metric,key,metric,'log2(max/min)',s[metric], label='original independent partial replication; not exact published-value agreement',
                    interval_kind='confidence', interval_low=s[low], interval_high=s[high],
                    uncertainty_method='Original 95% pointwise whole-trial paired-bootstrap interval; conditional on grid/workers, no multiple-comparison adjustment.')
        for c in native['claims']:
            for metric,value in c['published_values'].items():
                units='ms' if metric.endswith('_ms') else 'fraction' if 'share' in metric else 'log2(max/min)'
                published(c['claim_id']+':'+metric,c['claim_id'],c['claim_id'],metric,units,value,c['figure'])
    elif module == 'reliable-sketch-study':
        for s in native['summaries']:
            for metric, values in s['statistics'].items():
                measurement(s['configuration_id']+':'+metric,s['configuration_id'],metric,values['units'],values['median'],
                    label='historical synthetic CPU measurement; original paper conditions not matched',
                    sample_count=values['n'], minimum=values['min'], maximum=values['max'], interval_kind='descriptive')
        # Mixed-unit publication entries are retained verbatim in native_miscellaneous;
        # they are not falsely flattened into comparable synthetic CPU observations.
    summaries = split_ipv6_coverage(native['summaries']) if module == 'ipv6-dns-study' else native['summaries']
    for i,s in enumerate(summaries):
        if module == 'ipv6-dns-study':
            values=list(s['statistics'].values()); coverage={(v['planned'],v['n'],v['missing'],v['invalid']) for v in values}
            if len(coverage)!=1: raise ValueError('IPv6 metrics have different coverage; split rather than conflate.')
            planned,recorded,missing,invalid=next(iter(coverage)); ambiguous=excluded=0
            interval='descriptive'; uncertainty='Daily min/max are descriptive; dates and NS sets are correlated, and no inferential interval is supported.'
        elif module == 'reliable-sketch-study':
            planned=s['planned'];recorded=s['recorded'];invalid=s['failed'];excluded=s['excluded'];missing=planned-recorded-invalid-excluded;ambiguous=0
            interval='descriptive';uncertainty='Seed min/max are descriptive; ten seeds share one stream and host. Missing per-metric sample counts remain in statistics.'
        elif module == 'http2-compliance-study':
            planned=s['planned'];missing=s['missing'];ambiguous=s['unknown'];recorded=s['recorded']-ambiguous;invalid=excluded=0
            interval='none';uncertainty='Finite archive census; unknown worker states are ambiguous, not silent drops or independent population samples.'
        elif module == 'leo-emergency-failover':
            planned=recorded=s['snapshots'];missing=invalid=ambiguous=excluded=0
            interval='descriptive';uncertainty='Adjacent orbital states are correlated deterministic simulations; min/max are not confidence intervals.'
        else:
            planned=s['expected_pairs'];recorded=s['matched_pairs'];missing=planned-recorded;invalid=ambiguous=excluded=0
            interval='confidence';uncertainty='Original 95% pointwise paired bootstrap retains whole trials within assignment strata. Finite grid and shared workers limit inference.'
        normalized = s
        if module == 'ipv6-dns-study':
            # Equal coverage is normalized once in the summary columns, rather
            # than repeated for every metric. All original numerical statistics
            # and metric-specific denominator units remain retained.
            normalized = dict(s, statistics={metric: {key:value for key,value in values.items()
                if key not in ('planned','n','missing','invalid','interval_kind')}
                for metric,values in s['statistics'].items()})
        add('summaries', str(i), run_id=audit_run, metric='Imported native aggregate '+str(i+1), units='metric-specific; see preserved statistics',
            statistics=dict(native_aggregate=normalized, native_relation=definition['prefix']+'summaries'), interval_kind=interval,
            uncertainty_method=uncertainty, planned=planned, unexpected=0, recorded=recorded, missing=missing,
            invalid=invalid, ambiguous=ambiguous, excluded=excluded)
    run = dict(id=audit_run, campaign_id=campaign, configuration_id=None, status='complete', reason=None,
        started_at=tick, ended_at=now(), analysis_version=VERSION, input_sha256=digest(raw), output_sha256=None,
        requested_resources=dict(action='Historical register transfer; no experiment hosts or model calls requested'),
        actual_resources=dict(action='Local deterministic schema/coverage projection'),
        usage=dict(measured_charges_usd=None, model_tokens=None, logical_input_bytes=len(raw)),
        provenance=dict(data_origin='archived-data', execution_origin='outside-web-app; historical imported assessment',
            audit='This run times the import projection only; native experiment dates/runs remain in the original database.',
            native_relation_counts=counts, native_export_sha256=digest(raw), original_exports=doc['native_export_url'],
            implementation_sha256=digest(Path(__file__).read_bytes()), import_protocol_sha256=declared_protocol_sha))
    records.append(dict(kind='runs', record=validate_record('runs', run)))
    records.sort(key=lambda row: list(FIELDS).index(row['kind']))
    if len(records)>5000 or len(canonical(records))>3_000_000:
        raise ValueError(f'Shared projection exceeds the frozen bound ({len(records)} records; {len(canonical(records))} bytes); do not truncate.')
    return dict(version=1, study_id=study, paper=paper, records=records, original_artifact=Path(export_path).name,
        input_sha256=digest(raw), coverage=dict(Counter(row['kind'] for row in records)),
        claim_labels=dict(Counter(row['record']['label'] for row in records if row['kind']=='assessments')),
        provenance=dict(version=VERSION, producer='Codex trusted historical evidence importer',
            native_module=module, native_counts=counts, shared_scope=shared_scope,
            original_export_sha256=digest(raw), import_protocol_sha256=declared_protocol_sha,
            scientific_execution='None; transfer only', reviewer=REVIEWER))
