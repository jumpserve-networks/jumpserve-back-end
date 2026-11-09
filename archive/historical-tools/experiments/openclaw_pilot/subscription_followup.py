"""One explicit JSON-mode request correction, preserving the original ledger."""
import argparse
import contextlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
from unittest.mock import patch

import subscription as sub

ROOT=sub.ROOT
ORIGINAL=ROOT / 'artifacts/v7'
BASE=ROOT / 'artifacts/v8'
PROTOCOL=ROOT / 'protocol-v8.json'
MIGRATION=sub.lab.INFRA / 'database/202610200006_subscription_recovery.sql'
ORIGINAL_REQUEST=sub.response_request
MESSAGE="Response input messages must contain the word 'json' in some form to use 'text.format' of type 'json_object'."


def response_request(logical):
    value=ORIGINAL_REQUEST(logical)
    value['input']=[dict(role='developer',content='Return exactly one JSON object following the decision schema in the instructions.')] + value['input']
    return value


def verified_rejection(row):
    raw=bytes.fromhex(row['response_hex']) if row.get('response_hex') is not None else None
    if row.get('status')!='failed' or row.get('http_status')!=400 or raw is None or sub.lab.sha(raw)!=row.get('response_sha256'):
        raise ValueError('A confirmed, byte-verified HTTP 400 rejection is required')
    error=json.loads(raw).get('error') or {}
    if error.get('type')!='invalid_request_error' or error.get('param')!='input' or error.get('message')!=MESSAGE:
        raise ValueError('Only the exact missing-JSON-input rejection is covered')
    return raw


class Provider(sub.Provider):
    def __init__(self,packet,**kwargs):
        super().__init__(packet,**kwargs)
        self.deadline=time.monotonic()+max(0,(sub.lab.dt.datetime.fromisoformat(packet['original_campaign_deadline_at'])-
                                            sub.lab.dt.datetime.now(sub.lab.dt.timezone.utc)).total_seconds())

    def query(self,sql):
        marker="count(*) filter(where status<>'complete')"
        if marker in sql:
            rejection=self.packet['rejection']
            receipt=super().query('select count(*) from research_subscription_recoveries where id='+
                sub.lab.database.sql_text(self.packet['recovery_id'])+' and protocol_sha256='+
                sub.lab.database.sql_text(self.packet['followup_protocol_sha256']))
            if receipt!='1':raise ValueError('Frozen recovery receipt is missing; no model admission')
            exception='id='+sub.lab.database.sql_text(rejection['id'])+" and status='failed' and http_status=400 and response_sha256="+sub.lab.database.sql_text(rejection['response_sha256'])
            sql=sql.replace(marker,"count(*) filter(where status<>'complete' and not ("+exception+'))')
        return super().query(sql)


@contextlib.contextmanager
def configuration():
    with patch.object(sub,'BASE',BASE),patch.object(sub,'PROTOCOL',PROTOCOL),patch.object(sub,'response_request',response_request),sub.configuration():
        yield


def prepare():
    if (BASE / 'frozen.json').exists():raise ValueError('Preserve the existing frozen follow-up')
    packet=json.loads((ORIGINAL / 'frozen.json').read_bytes());sub.verify_frozen(packet)
    raw_report=(ORIGINAL / 'report.json').read_bytes();report=json.loads(raw_report)
    if report['passed'] or len(report['calls'])!=1:raise ValueError('Original outcome differs')
    query=lambda sql:sub.lab.database.query(sql,database=packet['coordinator_database'])
    pid=sub.lab.database.sql_text(packet['id'])
    calls=json.loads(query("select json_agg(t) from (select id,status,http_status,response_sha256,encode(response_raw,'hex') response_hex from research_subscription_calls where pilot_id="+pid+' order by number)t'))
    if len(calls)!=1:raise ValueError('Original call count differs')
    raw=verified_rejection(calls[0])
    if sub.lab.sha(raw)!=json.loads(PROTOCOL.read_bytes())['original_rejection_sha256'] or (ORIGINAL / 'openclaw-runtime/provider-01/response.bin').read_bytes()!=raw:
        raise ValueError('Original retained rejection bytes differ')
    with patch.object(sub.lab,'BASE',ORIGINAL):
        sub.lab.activate(packet['arm'],packet)
    task=sub.lab.autonomy.one('autonomy_tasks',packet['arm']['study_id'],packet['arm']['task_id'])
    workflow=sub.lab.autonomy.rows('autonomy_workflows',packet['arm']['study_id'])[0]
    if task['status']!='blocked' or task['attempts']!=1 or task['stage']!='planning' or workflow['max_attempts']!=25 or workflow['max_spend_cents']!=10000:
        raise ValueError('Original task or fixed paper limits differ')
    deadline=sub.lab.dt.datetime.fromisoformat(report['started_at'])+sub.lab.dt.timedelta(seconds=2700)
    if (deadline-sub.lab.dt.datetime.now(sub.lab.dt.timezone.utc)).total_seconds()<180:
        raise ValueError('Original campaign admission window has expired; no deadline extension')
    BASE.mkdir(parents=True,exist_ok=True)
    for name in ('inputs','openclaw-runtime'):
        shutil.copytree(ORIGINAL / name,BASE / name,dirs_exist_ok=False)
    packet.update(rejection=calls[0],recovery_id=str(uuid.uuid4()),original_report_sha256=sub.lab.sha(raw_report),
        original_campaign_deadline_at=deadline.isoformat(),followup_protocol_sha256=sub.lab.sha(PROTOCOL.read_bytes()),
        original_task=task,original_workflow=workflow)
    # Another conservative $10 overhead, with all original requests still counted.
    packet['prior_committed_cents']+=1000
    additions=[PROTOCOL,MIGRATION,Path(__file__),ROOT / 'test_subscription_followup.py',ORIGINAL / 'report.json']
    for path in additions:
        relative=str(path.relative_to(sub.lab.BACKEND.parent));packet['files'][relative]=sub.lab.sha(path.read_bytes())
        destination=BASE / 'inputs' / relative;destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(path.read_bytes())
    sub.lab.save(BASE / 'frozen.json',packet)
    print(json.dumps(dict(frozen=str(BASE / 'frozen.json'),model_calls=0,original_attempts=task['attempts'],original_deadline=deadline.isoformat())))


def live():
    packet=json.loads((BASE / 'frozen.json').read_bytes());sub.verify_frozen(packet)
    if (BASE / 'report.json').exists():raise ValueError('Follow-up already attempted; no replay')
    gate=json.loads((BASE / 'gate.json').read_bytes())
    if not gate['passed'] or any(sub.lab.sha((sub.lab.BACKEND.parent / name).read_bytes())!=digest for name,digest in gate['files'].items()):
        raise ValueError('Follow-up software gate is missing or changed')
    selected=sub.catalog();provider=Provider(packet);query=provider.query;pid=sub.lab.database.sql_text(packet['id'])
    query(MIGRATION.read_text())
    task=packet['original_task'];workflow=packet['original_workflow'];arm=packet['arm']
    sub.lab.activate(arm,packet)
    if sub.lab.autonomy.one('autonomy_tasks',arm['study_id'],arm['task_id'])!=task or sub.lab.autonomy.rows('autonomy_workflows',arm['study_id'])[0]!=workflow:
        raise ValueError('Original task changed after freeze')
    receipt=dict(reviewer=json.loads(PROTOCOL.read_bytes())['reviewer'],original_task=task,original_workflow=workflow,
                 original_call_status=packet['rejection']['status'],same_turn_replay=False,counters_preserved=True,deadline_preserved=True)
    if packet.get('stream_parser'):receipt['stream_parser']=packet['stream_parser']
    raw=PROTOCOL.read_bytes();original_report=(ORIGINAL / 'report.json').read_bytes()
    query('begin; insert into research_subscription_recoveries(id,pilot_id,rejected_call_id,protocol_raw,protocol_sha256,original_report_raw,original_report_sha256,rejection_sha256,reserved_overhead_cents,receipt) values('+','.join([
        sub.lab.database.sql_text(packet['recovery_id']),pid,sub.lab.database.sql_text(packet['rejection']['id']),sub.sql_bytes(raw),sub.lab.database.sql_text(sub.lab.sha(raw)),
        sub.sql_bytes(original_report),sub.lab.database.sql_text(sub.lab.sha(original_report)),sub.lab.database.sql_text(packet['rejection']['response_sha256']),str(packet.get('additional_overhead_cents',1000)),sub.lab.database.sql_json(receipt)])+
        "); update research_subscription_pilots set status='running',reason='Versioned JSON-input correction; original admission time and failed call retained.' where id="+pid+'; commit;')
    study=sub.lab.database.sql_text(arm['study_id']);tid=sub.lab.database.sql_text(arm['task_id'])
    sub.lab.database.query("begin; insert into research_autonomy_events(study_id,task_id,event,details) values("+study+','+tid+
        ",'lab-subscription-explicit-request-correction',"+sub.lab.database.sql_json(receipt)+
        "); update research_autonomy_tasks set status='queued',next_at=clock_timestamp(),reason='Versioned JSON input correction; next numbered attempt, fixed counters retained.' where id="+tid+
        "; update research_autonomy_workflows set status='running',reason='Explicit request correction; no budget, counter or deadline reset.' where study_id="+study+'; commit;')
    after_task=sub.lab.autonomy.one('autonomy_tasks',arm['study_id'],arm['task_id']);after_workflow=sub.lab.autonomy.rows('autonomy_workflows',arm['study_id'])[0]
    for field in ('attempts','context','claim_id','label','created_at'):
        if after_task[field]!=task[field]:raise ValueError('Original task counter/evidence changed')
    for field in ('committed_cents','max_spend_cents','max_attempts','deadline_at','initial_prompt','initial_sha256','created_at'):
        if after_workflow[field]!=workflow[field]:raise ValueError('Original paper limits changed')
    report=dict(version=json.loads(raw)['id'],started_at=sub.lab.stamp(),passed=False,catalog=selected,
                original_report_sha256=sub.lab.sha(original_report),original_protocol_sha256=sub.lab.sha(raw),
                original_campaign_deadline_at=packet['original_campaign_deadline_at'],recovery=receipt,
                production_mutations=0,measured_charges_usd=None,subscription_allocation_usd=None,paid_api_fallback=False)
    sub.lab.save(BASE / 'report.json',report);http,thread=sub.lab.server(provider)
    try:
        report['trajectory']=sub.lab.trajectory(provider,arm,packet)
        report['audit']=sub.verify_audit(provider,packet)
        rows=json.loads((BASE / 'openclaw-runtime/measurements.json').read_bytes());oracle=bool(rows)
        for row in rows:
            with sub.lab.localcontext() as context:
                context.prec=100;details=row['details'];difference=sub.lab.Decimal(str(row['value']))-sub.lab.Decimal(details['published'])
                oracle &= row['status']=='recorded' and difference==sub.lab.Decimal(details['difference']) and abs(difference)<=sub.lab.Decimal(details['tolerance_absolute'])
        trajectory=report['trajectory'];coverage=trajectory.get('assessment',{}).get('tested_conditions',{}).get('coverage',{})
        report.update(covered_cells=len(rows),planned_cells=1152,decimal_oracle=oracle)
        report['passed']=trajectory.get('task_status')=='resolved' and trajectory.get('label')=='reproduced' and len(rows)==1152 and coverage.get('covered_cells')==1152 and oracle and not provider.stop_status
    except Exception as error:
        report.update(error=type(error).__name__,reason=str(error))
    finally:
        http.shutdown();thread.join(timeout=5);http.server_close()
        report['calls']=json.loads(query("select coalesce(json_agg(t),'[]') from (select number,status,usage,reserved_cents,estimated_cents,http_status,error_code,request_sha256,response_sha256,started_at,ended_at from research_subscription_calls where pilot_id="+pid+' order by number)t'))
        report.update(ended_at=sub.lab.stamp(),pause_status=provider.stop_status,
            aggregate_committed_cents=packet['prior_committed_cents']+1000+sum(call['reserved_cents'] for call in report['calls']),
            missing_usage_calls=sum(call['usage'] is None for call in report['calls']))
        sub.lab.save(BASE / 'report.json',report)
        query('update research_subscription_pilots set status='+sub.lab.database.sql_text('complete' if report['passed'] else 'paused')+
              ',ended_at=clock_timestamp(),report='+sub.lab.database.sql_json(report)+' where id='+pid)
        print(json.dumps(dict(report=str(BASE / 'report.json'),passed=report['passed'],calls=len(report['calls']),pause_status=provider.stop_status)))


def check(additional_paths=()):
    if (BASE / 'gate.json').exists():raise ValueError('Existing gate is immutable; retain it')
    paths=[Path(__file__),PROTOCOL,MIGRATION,ROOT / 'test_subscription_followup.py']+list(additional_paths)
    hashes={str(path.relative_to(sub.lab.BACKEND.parent)):sub.lab.sha(path.read_bytes()) for path in paths}
    result=subprocess.run([sys.executable,'-B','-m','unittest','discover','-s','experiments/openclaw_pilot','-p','test_*.py','-v'],
                          cwd=sub.lab.BACKEND,capture_output=True,timeout=180)
    BASE.mkdir(parents=True,exist_ok=True)
    (BASE / 'gate-stdout.bin').write_bytes(result.stdout);(BASE / 'gate-stderr.bin').write_bytes(result.stderr)
    sub.lab.save(BASE / 'gate.json',dict(passed=result.returncode==0,files=hashes,model_calls=0,ended_at=sub.lab.stamp()))
    print(json.dumps(dict(passed=result.returncode==0,model_calls=0,report=str(BASE / 'gate.json'))))
    if result.returncode:raise SystemExit(1)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['prepare','check','run']);args=parser.parse_args()
    if args.action=='prepare':prepare()
    elif args.action=='check':check()
    else:
        with configuration():live()
