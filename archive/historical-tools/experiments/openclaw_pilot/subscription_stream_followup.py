"""Explicit stream-parser follow-up; original identities and limits retained."""
import argparse
import contextlib
import copy
import io
import json
from pathlib import Path
import shutil
import time
import uuid
from unittest.mock import patch

import subscription as sub
import subscription_followup as previous
import subscription_stream as parser

ROOT=sub.ROOT
ORIGINAL=ROOT / 'artifacts/v8'
BASE=ROOT / 'artifacts/v9'
PROTOCOL=ROOT / 'protocol-v9.json'
MIGRATION=sub.lab.INFRA / 'database/202610200007_subscription_stream_review.sql'


class Provider(previous.Provider):
    def query(self,sql):
        marker="count(*) filter(where status<>'complete')"
        if marker in sql:
            identities=','.join(sub.lab.database.sql_text(self.packet[key]) for key in ('previous_recovery_id','recovery_id'))
            count=sub.Provider.query(self,'select count(*) from research_subscription_recoveries where pilot_id='+
                sub.lab.database.sql_text(self.packet['id'])+' and id in ('+identities+')')
            if count!='2':raise ValueError('Both immutable recovery receipts are required')
            predicate="status<>'complete' and not exists(select 1 from research_subscription_recoveries receipt where receipt.pilot_id=research_subscription_calls.pilot_id and receipt.rejected_call_id=research_subscription_calls.id and receipt.rejection_sha256=research_subscription_calls.response_sha256 and receipt.id in ("+identities+'))'
            sql=sql.replace(marker,'count(*) filter(where '+predicate+')')
        return sub.Provider.query(self,sql)


@contextlib.contextmanager
def configuration():
    with patch.object(previous,'BASE',BASE),patch.object(previous,'ORIGINAL',ORIGINAL),\
         patch.object(previous,'PROTOCOL',PROTOCOL),patch.object(previous,'MIGRATION',MIGRATION),\
         patch.object(previous,'Provider',Provider),patch.object(sub,'read_stream',parser.read_stream),previous.configuration():
        yield


def prepare():
    if (BASE / 'frozen.json').exists():raise ValueError('Preserve the existing frozen parser follow-up')
    packet=json.loads((ORIGINAL / 'frozen.json').read_bytes())
    changed=str((ROOT / 'subscription_followup.py').relative_to(sub.lab.BACKEND.parent))
    for name,digest in packet['files'].items():
        snapshot=ORIGINAL / 'inputs' / name
        if sub.lab.sha(snapshot.read_bytes())!=digest:raise ValueError('Original frozen snapshot differs')
        if name!=changed and sub.lab.sha((sub.lab.BACKEND.parent / name).read_bytes())!=digest:
            raise ValueError('Unexpected source drift: '+name)
    raw_report=(ORIGINAL / 'report.json').read_bytes();report=json.loads(raw_report)
    if report['passed'] or len(report['calls'])!=2:raise ValueError('Original stream failure differs')
    query=lambda sql:sub.lab.database.query(sql,database=packet['coordinator_database'])
    calls=json.loads(query("select json_agg(t) from (select id,status,http_status,response_sha256,encode(response_raw,'hex') response_hex from research_subscription_calls where pilot_id="+sub.lab.database.sql_text(packet['id'])+' order by number)t'))
    if len(calls)!=2 or calls[1]['status']!='invalid-output' or calls[1]['http_status']!=200:
        raise ValueError('Expected one retained request rejection and one parser failure')
    raw=bytes.fromhex(calls[1]['response_hex'])
    if sub.lab.sha(raw)!=json.loads(PROTOCOL.read_bytes())['original_stream_sha256']:
        raise ValueError('Exact retained completed stream required')
    sink=io.BytesIO();corrected=parser.read_stream(io.BytesIO(raw),sink,time.monotonic()+10,1048576)
    answer=sub.structured_response(corrected)
    if sink.getvalue()!=raw or sub.lab.model.parse(answer)['action']!='choose-check':
        raise ValueError('Retained actual stream did not yield the expected valid planning decision')
    with patch.object(sub.lab,'BASE',ORIGINAL):sub.lab.activate(packet['arm'],packet)
    arm=packet['arm'];task=sub.lab.autonomy.one('autonomy_tasks',arm['study_id'],arm['task_id']);workflow=sub.lab.autonomy.rows('autonomy_workflows',arm['study_id'])[0]
    if task['status']!='blocked' or task['attempts']!=2 or task['stage']!='planning' or workflow['max_attempts']!=25:
        raise ValueError('Original task state/counters differ')
    deadline=sub.lab.dt.datetime.fromisoformat(packet['original_campaign_deadline_at'])
    if (deadline-sub.lab.dt.datetime.now(sub.lab.dt.timezone.utc)).total_seconds()<180:raise ValueError('Original admission window expired')
    BASE.mkdir(parents=True,exist_ok=True)
    for name in ('inputs','openclaw-runtime'):shutil.copytree(ORIGINAL / name,BASE / name,dirs_exist_ok=False)
    packet.update(previous_recovery_id=packet['recovery_id'],recovery_id=str(uuid.uuid4()),rejection=calls[1],
        original_task=task,original_workflow=workflow,original_report_sha256=sub.lab.sha(raw_report),
        followup_protocol_sha256=sub.lab.sha(PROTOCOL.read_bytes()),stream_parser=parser.VERSION,additional_overhead_cents=0)
    paths=[PROTOCOL,MIGRATION,Path(__file__),ROOT / 'subscription_stream.py',ROOT / 'subscription_followup.py',ROOT / 'test_subscription_stream.py',ORIGINAL / 'report.json']
    for path in paths:
        relative=str(path.relative_to(sub.lab.BACKEND.parent));packet['files'][relative]=sub.lab.sha(path.read_bytes())
        destination=BASE / 'inputs' / relative;destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(path.read_bytes())
    sub.lab.save(BASE / 'retained-stream-reanalysis.json',dict(version=parser.VERSION,original_response_sha256=sub.lab.sha(raw),
        reconstructed_response=corrected,validated_logical_answer=answer,actual_new_provider_calls=0,
        scope='Derived reconstruction of one retained actual model answer, not a new inference, reused answer under a new prompt or scientific measurement.'))
    sub.lab.save(BASE / 'frozen.json',packet)
    print(json.dumps(dict(frozen=str(BASE / 'frozen.json'),original_attempts=task['attempts'],actual_new_model_calls=0)))


if __name__=='__main__':
    command=argparse.ArgumentParser(description=__doc__);command.add_argument('action',choices=['prepare','check','run']);args=command.parse_args()
    if args.action=='prepare':prepare()
    elif args.action=='check':
        with configuration():previous.check([Path(__file__),ROOT / 'subscription_stream.py',ROOT / 'test_subscription_stream.py'])
    else:
        with configuration():previous.live()
