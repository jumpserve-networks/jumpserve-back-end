"""Publish aggregate local pilot evidence, excluding raw private source material."""
from collections import Counter
import datetime as dt
import hashlib
import json
from pathlib import Path
import run as pilot

ROOT = Path(__file__).resolve().parent


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    source = ROOT / 'artifacts/v4/report.json'
    report = json.loads(source.read_bytes())
    if 'ended_at' not in report or len(report['arms']) != 2:
        raise ValueError('Preserve the running campaign; no completed-result export yet')
    packet = json.loads((source.parent / 'frozen.json').read_bytes())
    v2 = json.loads((ROOT / 'artifacts/v2/report.json').read_bytes())
    calls = report['calls']
    public = dict(version='openclaw-ipv6-runtime-pilot-results-v4', completed_at=report['ended_at'],
        comparison_status='feasibility-passed' if report['passed'] else 'incomplete-or-failed-feasibility',
        question=packet['protocol']['question'], data_origin='archived-reanalysis',
        source=packet['protocol']['source'], runtime=packet['protocol']['configuration'],
        baseline='Current local JumpServe controller, including the uncommitted context-budget correction, not a byte-identical production deployment.',
        design=packet['protocol']['design'], targets=report['targets'], criteria=report['checks'],
        original_bytes_after_retrieval=report.get('audit_verification'), arms=[],
        aggregate=dict(actual_provider_calls=len(calls), provider_statuses=dict(Counter(c['status'] for c in calls)),
            conservative_reserved_cents=report['reserved_cents'], maximum_reserved_cents=10000,
            known_conservative_estimated_model_cents=report['known_estimated_model_cents'],
            missing_usage_calls=report['missing_usage_calls'], measured_charges_usd=None,
            local_compute_charges_usd=None, local_storage_charges_usd=None,
            experiment_instances=0, production_mutations=0),
        operational_history=[
            dict(protocol='v1', outcome='Stopped by invalid arm identity before a paid provider call; original snapshots retained.'),
            dict(protocol='v2', outcome='Eight valid decisions and two numerical checks; ninth provider request explicitly throttled. Paired comparison stopped.'),
            dict(protocol='v3', outcome='Preparation excluded after the original evaluation window expired; no recovery or model call.'),
            dict(protocol='v4', outcome='Separate paced follow-up on the same tasks, with original seven-day paper deadlines, attempts and all shared commitments retained.')],
        evaluator=dict(identity='Codex primary AI implementer', type='AI', independent=False, human=False),
        evidence_report_sha256=digest(source.read_bytes()), frozen_inputs_sha256=digest((source.parent/'frozen.json').read_bytes()),
        limitations=[
            'One previously exposed claim and one continued trajectory per runtime; no independent or held-out validation.',
            'The same controller and archived inputs are shared. Decimal checks share those inputs; original historical measurements are not independently validated.',
            'Only bounded structured model decisions are evaluated. OpenClaw native tools, persistent conversational autonomy, parallel subagents and natural-language chat answers are not tested.',
            'Runtime-specific system instructions differ. Costs and elapsed times describe these configurations and cannot establish a causal framework ranking.',
            'Recorded execution wall times include pacing and exclude the gap between separately declared evaluation windows. Calendar elapsed time also includes the authentication interruption.',
            'Provider HTTP response wire bytes are unavailable; canonical SDK requests, original CLI response bytes and submitted OpenClaw HTTP bodies are retained.',
            'Local Google identity, Storage/HTTP transport and prompt activation are explicit fixtures. Remote Supabase persistence, real user authentication and web integration remain unverified.',
            'No new adversarial prompt-injection evaluation or prompt publication. Public-site isolation remains deferred.',
            'Missing usage and measured charges remain unknown. Conservative reservations and estimates are not invoice charges.',
            'Scientific agreement covers the frozen numerical check only; other paper claims and complete literature/packet/causal/operational coverage remain outside the pilot.'])
    for arm in report['arms']:
        name = arm['name']; folder = source.parent / name
        measurements = json.loads((folder / 'measurements.json').read_bytes())
        tasks = json.loads((folder / 'autonomy_tasks.json').read_bytes())
        state = json.loads((folder / 'autonomy_workflows.json').read_bytes())[0]
        task = next(t for t in tasks if t['claim_id'])
        runs = json.loads((folder / 'runs.json').read_bytes())
        current_calls = [c for c in calls if c['arm'] == name]
        previous_arm = next(a for a in v2['arms'] if a['name'] == name)
        usage = {}
        for field in ('inputTokens','outputTokens','totalTokens'):
            values = [(c.get('usage') or {}).get(field) for c in current_calls]
            usage[field] = dict(known_sum=sum(v for v in values if isinstance(v,int)), missing_calls=sum(v is None for v in values))
        public['arms'].append(dict(name=name, status=arm['status'], terminal_task_status=task['status'],
            claim_label=task['label'], controller_attempts=task['attempts'], provider_calls=len(current_calls),
            complete_runs=sum(r['status']=='complete' for r in runs),
            matched_measurements=len(measurements), measurement_statuses=dict(Counter(m['status'] for m in measurements)),
            recorded_units=sorted({m['units'] for m in measurements}),
            comparison_labels=dict(Counter(m['details']['label'] for m in measurements)),
            input_output_usage=usage, recorded_execution_wall_seconds=previous_arm['wall_seconds']+arm['wall_seconds'],
            workflow_max_attempts=state['max_attempts'], workflow_max_spend_cents=state['max_spend_cents'],
            paper_deadline_at=state['deadline_at'], workflow_committed_cents=state['committed_cents'],
            conservative_reserved_model_cents=sum(c['reserved_cents'] for c in current_calls),
            known_conservative_estimated_model_cents=sum(c['estimated_cents'] for c in current_calls if c['estimated_cents'] is not None)))
    path = ROOT / 'results-v4.json'
    path.write_text(json.dumps(public, indent=2)+'\n')
    entries=[]
    for original in sorted((ROOT/'artifacts').rglob('*')):
        if original.is_file():
            raw=original.read_bytes()
            entries.append(dict(path=str(original.relative_to(ROOT)), bytes=len(raw), sha256=digest(raw)))
    manifest=dict(version=1, captured_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        description='Manifest of private local original evidence, including successful, failed and excluded attempts. Hashes do not establish content review.',
        files=len(entries), logical_bytes=sum(e['bytes'] for e in entries), artifacts=entries,
        results_sha256=digest(path.read_bytes()), raw_artifacts_public=False)
    (ROOT/'artifacts-manifest-v4.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(dict(results=str(path), comparison_status=public['comparison_status'], artifacts=len(entries), logical_bytes=manifest['logical_bytes'])))


if __name__=='__main__':
    main()
