#!/usr/bin/env python3
"""Trusted local controller: normalized research records into JumpServe Supabase.

Uses the existing Supabase management login without printing or distributing it.
Worker results are read from a local copy of the dedicated private evidence bucket.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import ssl
import time
import urllib.error

from analysis import analyze, configuration_id
from audit_evidence import audit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]/'jumpserve-infra'/'scripts'))
from audit_supabase_security import PROJECT, query as management_query

TABLES = {
    'campaigns': ['id'], 'configurations': ['id'], 'config_flows': ['configuration_id','flow_index'],
    'trials': ['id'], 'flows': ['trial_id','flow_index'], 'samples': ['trial_id','flow_index','snapshot_index'],
    'papers': ['campaign_id','reference_number'], 'claims': ['campaign_id','claim_id'],
    'cells': ['configuration_id','flow_index'], 'summaries': ['campaign_id','family','cca_group','flow_index'],
    'latency_trials': ['campaign_id','worker_index','treatment'],
    'latency_samples': ['campaign_id','worker_index','treatment','packet_index'],
}
SEED_STATEMENTS = None


def query(sql):
    # Controller writes are idempotent upserts or assignments. Retry transport
    # failures without printing credentials or query payloads.
    for attempt in range(4):
        try:
            return management_query(sql)
        except (ssl.SSLError, urllib.error.URLError, TimeoutError):
            if attempt == 3:
                raise
            print('Transient database transport error; retrying.', flush=True)
            time.sleep(2**attempt)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def upsert(kind, rows):
    if not rows:
        return
    table = 'delay_study_'+kind
    key = TABLES[kind]
    columns = list(rows[0])
    if any(set(row) != set(columns) for row in rows):
        raise ValueError('Inconsistent normalized record fields')
    # Identifiers come from the controller's fixed record constructors, not data.
    if any(not name.replace('_','').isalnum() for name in columns):
        raise ValueError('Unexpected column name')
    names = ','.join('"'+c+'"' for c in columns)
    conflict = ','.join('"'+c+'"' for c in key)
    changes = ','.join(f'"{c}"=excluded."{c}"' for c in columns if c not in key)
    for start in range(0, len(rows), 1000):
        payload = json.dumps(rows[start:start+1000], allow_nan=False).replace("'", "''")
        sql = f'''insert into public.{table} ({names})
          select {names} from jsonb_populate_recordset(null::public.{table}, '{payload}'::jsonb)
          on conflict ({conflict}) do update set {changes};'''
        if SEED_STATEMENTS is not None:
            SEED_STATEMENTS.append(sql)
        else:
            query(sql)
    print(json.dumps({'table': table, 'rows': len(rows)}), flush=True)


def seed(manifest):
    campaign = manifest['id']
    upsert('campaigns', [{
        'id': campaign, 'title': 'Propagation Delay and Congestion Control',
        'paper_doi': '10.4230/OASIcs.NINeS.2026.27', 'status': 'planned',
        'protocol': (HERE/'PROTOCOL.md').read_text(), 'protocol_sha256': digest(HERE/'PROTOCOL.md'),
        'manifest_sha256': digest(HERE/'campaign-v1.json'), 'kernel_commit': manifest['kernel_commit'],
        'planned_trials': len(manifest['trials']), 'bootstrap_replicates': 2000, 'random_seed': manifest['seed'],
        'limitations': [
            'Finite balanced grids; not every possible propagation-delay assignment.',
            'Calibrated fixed ACK delay; the adaptive estimator in Algorithm 1 is not tested.',
            'Kernel version, goodput measurement and grid differ from the incompletely specified paper setup.',
            'Live application QoE, GCP paths and the Copa simulation are not assessed.',
            'Five whole-trial repetitions per cell; confidence intervals are conditional on the tested grid and workers.',
            'Reference retrieval and reading status are tracked individually; the literature review is incomplete.'
        ],
        'provenance': {'source_artifact_commit': '0cbb5d9edc1d28a7a6918aa4e0062459ac592c13',
                       'bbr_versions': {'bbr': 'BBRv3', 'bbr1': 'BBRv1'},
                       'instance_type': 'c7i.large', 'region': 'us-east-1',
                       'baseline_delay_semantics': 'Round-trip increment, applied once on the ACK-return path'}
    }])
    configs, config_flows = {}, []
    for trial in manifest['trials']:
        c = trial['config']
        identity = configuration_id(campaign, c)
        if identity in configs:
            continue
        configs[identity] = {'id': identity, 'campaign_id': campaign,
                             **{k: c[k] for k in ['family','treatment','capacity_mbps','queue_packets','duration_seconds','warmup_seconds']},
                             'cca_group': '/'.join(c['ccas']), 'target_rtt_ms': c.get('target_rtt_ms'),
                             'config_sha256': hashlib.sha256(json.dumps(c,sort_keys=True).encode()).hexdigest()}
        for i, (cca, delay) in enumerate(zip(c['ccas'], c['delays_ms']), 1):
            config_flows.append({'configuration_id': identity, 'flow_index': i, 'cca': cca, 'configured_base_rtt_ms': delay})
    upsert('configurations', list(configs.values()))
    upsert('config_flows', config_flows)
    # Do not run seed again after measurements have arrived: it creates planned rows.
    upsert('trials', [{'id': t['id'], 'campaign_id': campaign,
                      'configuration_id': configuration_id(campaign, t['config']),
                      'block_index': t['block_index'], 'replicate': t['replicate'],
                      'attempt_id': 'not-run', 'status': 'planned', 'start_order': t['start_order']}
                     for t in manifest['trials']])
    literature(manifest)
    claims = [
        ('forward_latency','Figure 3','ACK-only delay preserves uncongested forward packet latency.',
         {'added_delay_ms':60},'mechanism_test','UDP timestamp probes on one host test two directions, not every placement or application.'),
        ('bbr1_delta','Figure 4','Common target RTT reduces BBRv1 delay sensitivity.',
         {'baseline_delta':4.58,'proxy_delta':0.27},'partial_replication','Finite 5×5 grid and calibrated fixed ACK delay; paper grid/kernel/buffer incompletely specified.'),
        ('bbr3_delta','Figure 4','Common target RTT reduces BBRv3 delay sensitivity.',
         {'baseline_delta':4.9,'proxy_delta':0.38},'partial_replication','Finite 5×5 grid and calibrated fixed ACK delay; paper grid/kernel/buffer incompletely specified.'),
        ('shift','Figure 7a','A common 50 ms shift reduces heterogeneous CCA delay sensitivity.',
         {},'partial_replication','Endpoint assignments only; paper bars are not digitized as exact numerical targets.'),
        ('narrow','Figure 7b','Imperfect equalization to 40 ± 5 ms reduces sensitivity below one.',
         {'target_delta_upper_bound':1},'partial_replication','Endpoint assignments only; separate reduction and absolute-threshold questions.'),
        ('gcp','Figure 5','Equalization improves sharing across GCP-to-NYC Internet paths.',
         {'bbr3_long_share_before':0.792,'bbr3_long_share_after':0.51},'not_assessed','AWS-hosted namespace emulation does not reproduce the published Internet paths.'),
        ('copa','Figure 6','Equal-delay Copa flows remain sensitive to heterogeneous competition.',
         {'delta_lower_bound':1.3,'equal_domain_delta':0.19},'not_assessed','Requires the separate Copa/BBR ns-3 implementation and scenario.'),
        ('video','Table 1 / Figure 8','Delay manipulation changes application bandwidth and video stalls.',
         {},'not_assessed','Bulk TCP does not reproduce present-day YouTube/cloud-storage workloads or QoE. Figure 8/prose also disagree on one cell.')
    ]
    upsert('claims', [{'campaign_id': campaign, 'claim_id': key, 'figure': fig, 'description': desc,
                      'published_values': values, 'coverage': coverage, 'limitation': limitation}
                     for key,fig,desc,values,coverage,limitation in claims])


def literature(manifest):
    papers = json.loads((HERE/'literature.json').read_text())
    upsert('papers', [{'campaign_id': manifest['id'], 'reference_number': p['number'],
                      'citation': p['citation'], 'kind': p['kind'], 'source_url': p.get('resolved_url'),
                      'download_status': p['download_status'], 'reading_status': p['reading_status'],
                      'sha256': p.get('sha256'), 'pages': p.get('pages'), 'version_note': p.get('version_note'),
                      'reading_notes': p.get('reading_notes')} for p in papers])


def load_results(manifest, root):
    planned = {t['id']: t for t in manifest['trials']}
    results = []
    for path in sorted(root.glob('worker-*/*/result.json')):
        result = json.loads(path.read_text())
        if result['id'] not in planned or result['config'] != planned[result['id']]['config']:
            raise ValueError('Unexpected evidence configuration: '+str(path))
        results.append((path, result))
    if len({r['id'] for _,r in results}) != len(results):
        raise ValueError('Duplicate trial attempts require explicit review')
    return results


def ingest(manifest, root):
    receipt_path = root/'.ingest-receipts.json'
    receipts = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
    def record_receipts(paths):
        for path in paths:
            receipts[str(path.relative_to(root))] = digest(path)
        temporary = receipt_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(receipts, indent=2)+'\n')
        temporary.replace(receipt_path)
    trials, flows, samples = [], [], []
    changed = []
    for path, r in load_results(manifest, root):
        if receipts.get(str(path.relative_to(root))) == digest(path):
            continue
        changed.append(path)
        c = r['config']
        trials.append({'id': r['id'], 'campaign_id': manifest['id'],
                       'configuration_id': configuration_id(manifest['id'], c),
                       'block_index': r['block_index'], 'replicate': r['replicate'],
                       'attempt_id': r['attempt_id'], 'status': r['status'], 'start_order': r['start_order'],
                       'kernel_release': r['kernel'], 'runner_sha256': r['runner_sha256'],
                       'started_at': r['started_at'], 'finished_at': r['finished_at'],
                       'validation_errors': r['validation_errors'], 'cpu_metrics': r.get('cpu', []),
                       'error': r.get('error'), 'evidence_sha256': digest(path),
                       'evidence_location': 's3://jumpserve-nines2026-395567831870-us-east-1/campaigns/'+manifest['id']+'/'+str(path.relative_to(root))})
        for flow in r['flows']:
            i = flow['flow']
            cal = next(x for x in r['calibration'] if x['flow'] == i)
            flows.append({'trial_id': r['id'], 'flow_index': i,
                          **{k: flow[k] for k in ['cca','configured_base_rtt_ms','goodput_mbps','measured_seconds','received_bytes','retransmits']},
                          **{k: cal[k] for k in ['natural_min_rtt_ms','added_ack_delay_ms','effective_min_rtt_ms']}})
            for index, point in enumerate(flow['points']):
                samples.append({'trial_id': r['id'], 'flow_index': i, 'snapshot_index': index,
                                'start_seconds': point['start_seconds'], 'end_seconds': point['end_seconds'],
                                'interval_seconds': point['seconds'], 'received_bytes': point['bytes'],
                                'goodput_mbps': point['goodput_mbps']})
    upsert('trials', trials)
    upsert('flows', flows)
    upsert('samples', samples)
    record_receipts(changed)
    if trials:
        query("update public.delay_study_campaigns set status='running' where id='nines2026-balanced-v1' and status='planned'")
    for path in sorted(root.glob('worker-*/preflight/direction.json')):
        if receipts.get(str(path.relative_to(root))) == digest(path):
            continue
        latency_trials, latency_samples = [], []
        worker_index = int(path.parents[1].name.split('-')[-1])
        data = json.loads(path.read_text())
        valid = all(data['checks'].values())
        for record in data['records']:
            key = {'campaign_id': manifest['id'], 'worker_index': worker_index, 'treatment': record['treatment']}
            latency_trials.append({**key, 'sent_packets': record['sent'], 'received_packets': record['received'],
                                   'median_forward_ms': record['median_forward_ms'], 'median_rtt_ms': record['median_rtt_ms'],
                                   'validation_passed': valid})
            latency_samples.extend({**key, 'packet_index': p['index'], 'forward_ms': p['forward_ms'], 'rtt_ms': p['rtt_ms']}
                                   for p in record['points'])
        upsert('latency_trials', latency_trials)
        upsert('latency_samples', latency_samples)
        record_receipts([path])


def finalize(manifest, root, output):
    """Mark complete only after every frozen trial and preflight is verified."""
    evidence_audit = audit(root, require_complete=True)
    (root/'evidence-audit.json').write_text(json.dumps(evidence_audit, indent=2)+'\n')
    results = load_results(manifest, root)
    planned = {t['id']: t for t in manifest['trials']}
    if {r['id'] for _, r in results} != set(planned):
        raise ValueError('Cannot finalize: the frozen manifest is not fully collected')
    runner_hash = digest(HERE/'runner.py')
    for path, result in results:
        trial = planned[result['id']]
        if (result['status'] != 'completed' or result['validation_errors']
                or result['runner_sha256'] != runner_hash
                or result['block_index'] != trial['block_index']
                or result['replicate'] != trial['replicate']
                or result['start_order'] != trial['start_order']
                or path.parents[0].name != trial['id']
                or path.parents[1].name != f"worker-{trial['block_index'] % 8}"):
            raise ValueError('Cannot finalize: invalid trial provenance '+result['id'])
    for worker in range(8):
        path = root/f'worker-{worker}'/'preflight'/'preflight.json'
        report = json.loads(path.read_text())
        if report['status'] != 'passed' or report['runner_sha256'] != runner_hash:
            raise ValueError(f'Cannot finalize: worker {worker} preflight failed')
    if any(s['matched_pairs'] != s['expected_pairs'] for s in output['summaries']):
        raise ValueError('Cannot finalize: analysis has incomplete matched pairs')
    expected = {
        'trials': len(results),
        'flows': sum(len(r['flows']) for _, r in results),
        'samples': sum(len(f['points']) for _, r in results for f in r['flows']),
        'latency_trials': 24,
        'latency_samples': 24000,
    }
    campaign = manifest['id'].replace("'", "''")
    counts = query(f"""select
      (select count(*) from public.delay_study_trials where campaign_id='{campaign}' and status='completed') as trials,
      (select count(*) from public.delay_study_flows f join public.delay_study_trials t on t.id=f.trial_id where t.campaign_id='{campaign}') as flows,
      (select count(*) from public.delay_study_samples s join public.delay_study_trials t on t.id=s.trial_id where t.campaign_id='{campaign}') as samples,
      (select count(*) from public.delay_study_latency_trials where campaign_id='{campaign}' and validation_passed) as latency_trials,
      (select count(*) from public.delay_study_latency_samples where campaign_id='{campaign}') as latency_samples""")[0]
    if {k: int(v) for k,v in counts.items()} != expected:
        raise ValueError('Cannot finalize: normalized database counts differ from evidence')
    evidence = {r['id']: digest(path) for path,r in results}
    provenance = json.dumps({
        'analysis_output_sha256': digest(root/'analysis.json'),
        'analysis_source_sha256': digest(HERE/'analysis.py'),
        'analysis_python_version': sys.version.split()[0],
        'iperf_version_artifact_counts': evidence_audit['iperf_version_artifact_counts'],
        'kernel_build_hash_source': 'build/cca-sha256.txt in the private research evidence bucket',
        'kernel_bzimage_sha256': 'ffdacd5572c8f9bb49192ef29a0a5d840e431e79f095d6d5b20fa30b6f91a35a',
        'kernel_config_sha256': 'ae09a8d40cf313306a055bf7773f9d7a2974823f4c4a438fbc7ca1047866ad7b',
        'bbr_source_sha256': {
            'bbr': '04ba1e8b436b88eb0716f3d2a64a9be5a5d946ba3169a590d7bb533c320bdc51',
            'bbr1': 'f4f2e2826c59070b6ded5c41ac230fbd87ad1db168f35f8042db7d1204c6a435',
        },
        'source_artifact_swhid': 'swh:1:dir:748439634ef08b81cd32abcfdac79141814e4f9b',
        'source_artifact_git_tree_matches_paper': True,
        'audit_source_sha256': digest(HERE/'audit_evidence.py'),
        'raw_evidence_audit_sha256': digest(root/'evidence-audit.json'),
        'final_evidence_manifest_sha256': hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest(),
        'normalized_counts': expected,
    }).replace("'", "''")
    finished = max(r['finished_at'] for _,r in results).replace("'", "''")
    query(f"""update public.delay_study_campaigns
      set status='completed', finished_at='{finished}'::timestamptz,
          provenance=provenance || '{provenance}'::jsonb
      where id='{campaign}'""")
    print(json.dumps({'campaign': manifest['id'], 'status': 'completed', 'verified_counts': expected}))


def main():
    global SEED_STATEMENTS
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['seed','literature','ingest','analyze','finalize','verify'])
    parser.add_argument('--results', type=Path, default=HERE/'results'/'campaign-v1')
    args = parser.parse_args()
    if PROJECT != 'regphejnlvfpyokpniny':
        raise RuntimeError('Unexpected Supabase project')
    manifest = json.loads((HERE/'campaign-v1.json').read_text())
    if args.action == 'verify':
        print(json.dumps(query("select id,status,planned_trials from public.delay_study_campaigns")))
        print(json.dumps(query("select tablename,policyname,roles,cmd,qual from pg_policies where schemaname='public' and tablename like 'delay_study_%' order by tablename")))
        print(json.dumps(query("select 'trials' as relation,count(*) from public.delay_study_trials union all select 'flows',count(*) from public.delay_study_flows union all select 'samples',count(*) from public.delay_study_samples")))
    elif args.action == 'seed':
        if query("select id from public.delay_study_campaigns where id='nines2026-balanced-v1'"):
            raise RuntimeError('Campaign already exists; seed cannot overwrite measurements')
        # Seed all relations atomically: a failed request cannot leave a half-
        # initialized campaign that would prevent a safe retry.
        SEED_STATEMENTS = []
        seed(manifest)
        query('begin;\n'+'\n'.join(SEED_STATEMENTS)+'\ncommit;')
        SEED_STATEMENTS = None
        print('Campaign seed committed atomically.', flush=True)
    elif args.action == 'literature':
        literature(manifest)
    elif args.action == 'ingest':
        ingest(manifest, args.results)
    else:
        # A summary must never refer to evidence that the public trial relations
        # have not received. Incremental receipts make this inexpensive.
        ingest(manifest, args.results)
        output = analyze(manifest, [r for _,r in load_results(manifest, args.results)])
        (args.results/'analysis.json').write_text(json.dumps(output, indent=2, allow_nan=False)+'\n')
        upsert('cells', output['cells'])
        upsert('summaries', output['summaries'])
        print(json.dumps({'counts': output['counts'], 'missing': output['missing']}))
        if args.action == 'finalize':
            finalize(manifest, args.results, output)


if __name__ == '__main__':
    main()
