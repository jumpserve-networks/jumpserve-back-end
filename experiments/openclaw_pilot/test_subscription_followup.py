"""Exposed regressions for one explicit rejected-request correction."""
import copy
import datetime as dt
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parent))
import subscription as sub
import subscription_followup as followup


class Correction(unittest.TestCase):
    def test_json_mode_instruction_is_in_input_without_mutating_evidence(self):
        with sub.configuration():
            logical=sub.lab.model.request('Fixed core prompt',dict(task='planning',missing=None,zero=0))
            before=copy.deepcopy(logical);original=sub.response_request(logical);actual=followup.response_request(logical)
        self.assertEqual(before,logical)
        self.assertEqual(actual['input'][1:],original['input'])
        self.assertEqual(actual['instructions'],original['instructions'])
        self.assertEqual(actual['input'][0]['role'],'developer')
        self.assertIn('JSON',actual['input'][0]['content'])
        self.assertEqual(actual['tools'],[])

    def test_only_exact_confirmed_missing_json_rejection_is_eligible(self):
        raw=json.dumps(dict(error=dict(type='invalid_request_error',param='input',message=followup.MESSAGE))).encode()
        record=dict(status='failed',http_status=400,response_hex=raw.hex(),response_sha256=sub.lab.sha(raw))
        self.assertEqual(followup.verified_rejection(record),raw)
        for field,value in (('status','unknown'),('http_status',503),('response_sha256','0'*64),('response_hex',None)):
            with self.assertRaises(ValueError):followup.verified_rejection(dict(record,**{field:value}))
        other=json.dumps(dict(error=dict(type='invalid_request_error',param='input',message='Other rejection'))).encode()
        with self.assertRaises(ValueError):followup.verified_rejection(dict(record,response_hex=other.hex(),response_sha256=sub.lab.sha(other)))


class RecoveryDatabase(unittest.TestCase):
    def test_append_only_receipt_keeps_failed_call_count_and_does_not_waive_unknown_calls(self):
        f=sub.lab.database;f.DATABASE='jumpserve_research_ideas_test_recover_'+uuid.uuid4().hex[:20]
        f.query.__defaults__=(f.DATABASE,None);f.bootstrap();f.query(sub.MIGRATION.read_text());f.query(followup.MIGRATION.read_text())
        study,pid,cid,rid=(str(uuid.uuid4()) for _ in range(4));raw=b'{"synthetic":true}'
        f.rpc('research_create_study',dict(p_actor=f.ACTOR,p_id=study,p_document=dict(title='Synthetic recovery',paper_url='https://example.org/synthetic.pdf',domain='Software',scope='Development controls only',study_kind='paper')))
        f.query('insert into research_subscription_pilots(id,study_id,protocol_raw,protocol_sha256,configuration,prior_committed_cents,overhead_cents,maximum_cents,status) values('+','.join([
            f.sql_text(pid),f.sql_text(study),sub.sql_bytes(raw),f.sql_text(sub.lab.sha(raw)),"'{}'",'4715','1000','10000',"'paused'"])+')')
        error=json.dumps(dict(error=dict(type='invalid_request_error',param='input',message=followup.MESSAGE))).encode()
        def insert_call(identity,number,status,body):
            request=raw+str(number).encode()
            f.query('insert into research_subscription_calls(id,pilot_id,number,logical_raw,logical_sha256,request_raw,request_sha256,transport_raw,transport_sha256,status,reserved_cents,http_status,response_raw,response_sha256) values('+','.join([
                f.sql_text(identity),f.sql_text(pid),str(number),sub.sql_bytes(request),f.sql_text(sub.lab.sha(request)),sub.sql_bytes(request),f.sql_text(sub.lab.sha(request)),sub.sql_bytes(request),f.sql_text(sub.lab.sha(request)),f.sql_text(status),'100','400',sub.sql_bytes(body),f.sql_text(sub.lab.sha(body))])+')')
        insert_call(cid,1,'failed',error)
        f.query('insert into research_subscription_recoveries(id,pilot_id,rejected_call_id,protocol_raw,protocol_sha256,original_report_raw,original_report_sha256,rejection_sha256,reserved_overhead_cents,receipt) values('+','.join([
            f.sql_text(rid),f.sql_text(pid),f.sql_text(cid),sub.sql_bytes(raw),f.sql_text(sub.lab.sha(raw)),sub.sql_bytes(raw),f.sql_text(sub.lab.sha(raw)),f.sql_text(sub.lab.sha(error)),'1000',"'{}'"])+')')
        f.rejects(lambda:f.query("update research_subscription_recoveries set receipt='{}' where id="+f.sql_text(rid)))
        f.rejects(lambda:f.query('delete from research_subscription_recoveries where id='+f.sql_text(rid)))
        self.assertEqual(f.query('select status from research_subscription_calls where id='+f.sql_text(cid)),'failed')
        packet=dict(id=pid,coordinator_database=f.DATABASE,prior_committed_cents=5715,protocol=sub.protocol(),
            original_campaign_deadline_at=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=10)).isoformat(),
            rejection=dict(id=cid,response_sha256=sub.lab.sha(error)),recovery_id=rid,followup_protocol_sha256=sub.lab.sha(raw))
        provider=followup.Provider(packet,token_source=lambda:'synthetic-subscription')
        sql="select json_build_object('count',count(*),'reserved',coalesce(sum(reserved_cents),0),'unsafe',count(*) filter(where status<>'complete')) from research_subscription_calls where pilot_id="+f.sql_text(pid)
        state=json.loads(provider.query(sql));self.assertEqual(state,dict(count=1,reserved=100,unsafe=0))
        insert_call(str(uuid.uuid4()),2,'unknown',b'')
        state=json.loads(provider.query(sql));self.assertEqual(state,dict(count=2,reserved=200,unsafe=1))
        for role in ('anon','authenticated'):
            self.assertEqual(f.query("select has_table_privilege(current_user,'research_subscription_recoveries','SELECT,INSERT,UPDATE,DELETE')",role=role),'f')


if __name__=='__main__':unittest.main()
