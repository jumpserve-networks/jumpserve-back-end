"""Paced continuation after one verified service rejection. Local writes only."""
import argparse
import datetime as dt
import json
from pathlib import Path
import shutil
import subprocess
import time
import uuid
import run as pilot

ROOT = Path(__file__).resolve().parent
ORIGINAL = ROOT / 'artifacts/v2'
BASE = ROOT / 'artifacts/v4'
PROTOCOL = ROOT / 'protocol-v4.json'
MIGRATION = pilot.INFRA / 'database/202610200004_runtime_pilot_rejections.sql'


def prepare():
    if (BASE / 'frozen.json').exists():
        raise ValueError('Follow-up already frozen; preserve original evidence')
    packet = json.loads((ORIGINAL / 'frozen.json').read_bytes())
    pilot.verify_frozen(packet)
    report_raw = (ORIGINAL / 'report.json').read_bytes()
    original_report = json.loads(report_raw)
    if original_report['passed'] or len(original_report['calls']) != 9 or original_report['missing_usage_calls'] != 1:
        raise ValueError('Original failed campaign differs')
    rejection = ORIGINAL / 'jumpserve-direct/provider-09/stderr.bin'
    error = rejection.read_bytes()
    if b'(ThrottlingException)' not in error or b'reached max retries: 0' not in error or (rejection.parent / 'response.bin').read_bytes():
        raise ValueError('Only the retained explicit rejection can be reconciled')
    coordinator = packet['coordinator_database']
    query = lambda sql: pilot.database.query(sql, database=coordinator)
    pid = pilot.database.sql_text(packet['id'])
    calls = json.loads(query("select json_agg(t) from (select id,arm,number,status,reserved_cents,encode(response_raw,'hex') response from research_runtime_pilot_calls where pilot_id=" + pid + ' order by started_at)t'))
    bad = [row for row in calls if row['status'] != 'complete']
    if len(bad) != 1 or bad[0]['arm'] != 'jumpserve-direct' or bad[0]['number'] != 9 or bad[0]['status'] != 'failed' or bad[0]['response'] != '':
        raise ValueError('Actual retained rejection state differs')
    if query('select status from research_runtime_pilots where id=' + pid) != 'failed':
        raise ValueError('Original coordinator status differs')
    original_deadline = dt.datetime.fromisoformat(original_report['started_at']) + dt.timedelta(seconds=2700)
    BASE.mkdir(parents=True, exist_ok=True)
    packet.update(original_report_sha256=pilot.sha(report_raw), original_frozen_sha256=pilot.sha((ORIGINAL / 'frozen.json').read_bytes()),
        followup_protocol_sha256=pilot.sha(PROTOCOL.read_bytes()), followup_producer_sha256=pilot.sha(Path(__file__).read_bytes()),
        followup_migration_sha256=pilot.sha(MIGRATION.read_bytes()), original_evaluation_deadline_at=original_deadline.isoformat(),
        failure_sha256=pilot.sha(error), rejection_call_id=bad[0]['id'], original_call_count=len(calls), recovery=[])
    for arm_record in packet['arms']:
        source = ORIGINAL / arm_record['name']
        destination = BASE / arm_record['name']
        shutil.copytree(source, destination, dirs_exist_ok=False)
        pilot.BASE = BASE
        pilot.activate(arm_record, packet)
        task = pilot.autonomy.one('autonomy_tasks', arm_record['study_id'], arm_record['task_id'])
        state = pilot.autonomy.rows('autonomy_workflows', arm_record['study_id'])[0]
        expected_attempts = 9 if arm_record['name'] == 'jumpserve-direct' else 1
        if task['status'] != 'blocked' or task['stage'] != 'planning' or task['attempts'] != expected_attempts:
            raise ValueError('Original claim pause differs')
        if state['max_attempts'] != 25 or state['max_spend_cents'] != 10000:
            raise ValueError('Original paper limits differ')
        if (dt.datetime.fromisoformat(state['deadline_at'])-dt.datetime.now(dt.timezone.utc)).total_seconds() < 180:
            raise ValueError('The original paper deadline is exhausted; no reset permitted')
        packet['recovery'].append(dict(arm=arm_record['name'], task=task, workflow=state))
        blobs = {}
        for artifact in pilot.autonomy.rows('artifacts', arm_record['study_id'], limit=1000):
            raw_path = destination / 'final-originals' / artifact['sha256']
            if not raw_path.exists():
                raw_path = destination / 'originals' / artifact['sha256']
            if not raw_path.exists() or pilot.sha(raw_path.read_bytes()) != artifact['sha256']:
                raise ValueError('A prior Storage artifact is not available for continuation')
            key = '/storage/v1/object/' + pilot.store.BUCKET + '/' + artifact['storage_path']
            blobs[key] = dict(path=str(raw_path.relative_to(BASE)), sha256=artifact['sha256'])
        arm_record['storage'] = blobs
    for path in (PROTOCOL, ROOT / 'protocol-v3.json', MIGRATION, Path(__file__), ORIGINAL / 'frozen.json', ORIGINAL / 'report.json', rejection):
        destination = BASE / 'inputs' / path.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(path.read_bytes())
    pilot.save(BASE / 'frozen.json', packet)
    print(json.dumps(dict(frozen=str(BASE / 'frozen.json'), model_calls=0, original_paper_limits_preserved=True)))


class Paced(pilot.Provider):
    def __init__(self, packet):
        super().__init__(packet)
        self.deadline = time.monotonic() + max(0, (dt.datetime.fromisoformat(packet['followup_deadline_at'])-dt.datetime.now(dt.timezone.utc)).total_seconds())

    def call(self, request, wire=None):
        latest = self.query("select coalesce(max(extract(epoch from started_at)),0) from research_runtime_pilot_calls where pilot_id=" + pilot.database.sql_text(self.packet['id']))
        delay = max(0, 30-(time.time()-float(latest)))
        if delay:
            time.sleep(delay)
        return super().call(request, wire)


def live():
    packet = json.loads((BASE / 'frozen.json').read_bytes())
    pilot.verify_frozen(packet)
    for path, expected in ((PROTOCOL, packet['followup_protocol_sha256']), (MIGRATION, packet['followup_migration_sha256']),
                           (Path(__file__), packet['followup_producer_sha256'])):
        if pilot.sha(path.read_bytes()) != expected:
            raise ValueError('Frozen continuation changed')
    if pilot.sha((ORIGINAL / 'report.json').read_bytes()) != packet['original_report_sha256']:
        raise ValueError('Original failed report changed')
    target = BASE / 'report.json'
    if target.exists():
        raise ValueError('Continuation already attempted; never reset it')
    spec = pilot.importlib.util.spec_from_file_location('followup_target_guard', pilot.INFRA / 'bin/autonomy-release.py')
    release = pilot.importlib.util.module_from_spec(spec); spec.loader.exec_module(release)
    targets = release.guard.target()
    packet['followup_deadline_at'] = (dt.datetime.now(dt.timezone.utc)+dt.timedelta(seconds=2700)).isoformat()
    pilot.BASE = BASE
    provider = Paced(packet)
    pid = pilot.database.sql_text(packet['id'])
    query = provider.query
    query(MIGRATION.read_text())
    if query('select count(*) from research_runtime_pilot_amendments where pilot_id=' + pid) != '0':
        raise ValueError('A reconciliation already exists')
    receipt = dict(implementation='explicit-throttle-followup-v2', reviewer=json.loads((ROOT / 'protocol-v3.json').read_bytes())['reviewer'],
        original_status='failed', restored_status='rejected', rejection_call_id=packet['rejection_call_id'],
        original_failure_sha256=packet['failure_sha256'], response_bytes=0, same_turn_replayed=False,
        commitments_preserved=True, workflow_snapshots=packet['recovery'], provider_spacing_seconds=30,
        original_evaluation_deadline_at=packet['original_evaluation_deadline_at'], new_followup_deadline_at=packet['followup_deadline_at'],
        paper_deadlines_unchanged=True)
    protocol_raw = PROTOCOL.read_bytes(); original_report = (ORIGINAL / 'report.json').read_bytes()
    query("begin; insert into research_runtime_pilot_amendments(id,pilot_id,protocol_raw,protocol_sha256,original_report_raw,original_report_sha256,receipt) values(" +
        ','.join([pilot.database.sql_text(str(uuid.uuid4())), pid, "decode('"+protocol_raw.hex()+"','hex')", pilot.database.sql_text(pilot.sha(protocol_raw)),
                  "decode('"+original_report.hex()+"','hex')", pilot.database.sql_text(pilot.sha(original_report)), pilot.database.sql_json(receipt)]) +
        "); update research_runtime_pilot_calls set status='rejected',reason='Explicit ThrottlingException reviewed; original failed state and bytes retained in amendment. Unknown cost reservation retained; only a new numbered turn is permitted.' where id=" +
        pilot.database.sql_text(packet['rejection_call_id']) + " and status='failed'; update research_runtime_pilots set status='running' where id=" + pid + '; commit;')
    report = dict(id=packet['id'], protocol_sha256=pilot.sha(PROTOCOL.read_bytes()), targets=targets, started_at=pilot.stamp(), arms=[], passed=False,
        production_mutations=0, original_report_sha256=packet['original_report_sha256'], original_calls_preserved=9,
        measured_charges_usd=None, evaluator=receipt['reviewer'], human_review=False, recovery_verified=[])
    pilot.save(target, report)
    for arm_record, original in zip(packet['arms'], packet['recovery']):
        pilot.activate(arm_record, packet)
        study = pilot.database.sql_text(arm_record['study_id']); tid = pilot.database.sql_text(arm_record['task_id'])
        task = pilot.autonomy.one('autonomy_tasks', arm_record['study_id'], arm_record['task_id'])
        state = pilot.autonomy.rows('autonomy_workflows', arm_record['study_id'])[0]
        if task != original['task'] or state != original['workflow']:
            raise ValueError('Retained task changed since continuation freeze')
        pilot.database.query("begin; insert into research_autonomy_events(study_id,task_id,event,details) values(" + study + ',' + tid +
            ",'lab-pilot-explicit-rejection-continuation'," + pilot.database.sql_json(dict(protocol_sha256=report['protocol_sha256'],original_attempts=task['attempts'],commitments_preserved=True,deadline_preserved=True)) +
            "); update research_autonomy_tasks set status='queued',next_at=clock_timestamp(),reason='Next numbered lab pilot turn after explicit rejection reconciliation; original limits and evidence preserved.' where id=" +
            tid + " and status='blocked'; update research_autonomy_workflows set status='running',reason='Paced lab continuation; no attempt, budget or deadline reset.' where study_id=" + study + '; commit;')
        after_task = pilot.autonomy.one('autonomy_tasks', arm_record['study_id'], arm_record['task_id'])
        after_state = pilot.autonomy.rows('autonomy_workflows', arm_record['study_id'])[0]
        for field in ('attempts','context','created_at','claim_id','label'):
            if task[field] != after_task[field]: raise ValueError('Recovery changed original task ' + field)
        for field in ('initial_prompt','initial_sha256','committed_cents','max_spend_cents','max_attempts','deadline_at','created_at'):
            if state[field] != after_state[field]: raise ValueError('Recovery changed original workflow ' + field)
        report['recovery_verified'].append(dict(arm=arm_record['name'], attempts=task['attempts'], commitments=state['committed_cents'], original_limits_preserved=True))
    http, thread = pilot.server(provider)
    try:
        for arm_record in packet['arms']:
            report['arms'].append(pilot.trajectory(provider, arm_record, packet)); pilot.save(target, report)
        report.update(pilot.compare(packet, report['arms']))
        report['audit_verification'] = pilot.verify_audit(provider, packet)
    finally:
        http.shutdown(); thread.join(timeout=5); http.server_close()
        report['calls'] = json.loads(query("select coalesce(json_agg(t),'[]') from (select arm,number,status,reserved_cents,estimated_cents,usage,request_sha256,response_sha256,started_at,ended_at from research_runtime_pilot_calls where pilot_id="+pid+' order by started_at)t'))
        report['reserved_cents'] = 2000 + sum(c['reserved_cents'] for c in report['calls'])
        report['known_estimated_model_cents'] = sum(c['estimated_cents'] for c in report['calls'] if c['estimated_cents'] is not None)
        report['missing_usage_calls'] = sum(c['estimated_cents'] is None for c in report['calls'])
        report.update(ended_at=pilot.stamp(), review_status='AI implementer review pending; exposed follow-up, no independent/human review')
        pilot.save(target, report)
        query("update research_runtime_pilots set status=" + pilot.database.sql_text('complete' if report['passed'] else 'failed') +
              ',ended_at=clock_timestamp(),report=' + pilot.database.sql_json(report) + ' where id=' + pid)
        print(json.dumps(dict(report=str(target), passed=report['passed'], calls=len(report['calls']), reserved_cents=report['reserved_cents'])))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('operation', choices=['prepare','run']); args = parser.parse_args()
    {'prepare': prepare, 'run': live}[args.operation]()
