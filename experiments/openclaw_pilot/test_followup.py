"""Local controls for the explicit rejection amendment; no model calls."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest
import uuid

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import run as pilot
import followup


class Followup(unittest.TestCase):
    def test_actual_sql_keeps_rejection_bytes_and_append_only_receipt(self):
        f = pilot.database
        f.bootstrap()
        f.query(pilot.MIGRATION.read_text())
        f.query(followup.MIGRATION.read_text())
        pid, cid, aid = (str(uuid.uuid4()) for _ in range(3))
        raw = b'{"synthetic":true,"missing":null,"zero":0}'
        response = b''
        f.query("insert into research_runtime_pilots(id,protocol_raw,protocol_sha256,configuration,status,maximum_cents,overhead_cents) values(" +
                f.sql_text(pid) + ",decode('" + raw.hex() + "','hex')," + f.sql_text(pilot.sha(raw)) + ",'{}','failed',10000,1000)")
        f.query("insert into research_runtime_pilot_calls(id,pilot_id,arm,number,logical_request_sha256,request_raw,request_sha256,response_raw,response_sha256,status,reserved_cents) values(" +
            ','.join([f.sql_text(cid),f.sql_text(pid),"'jumpserve-direct'",'9',f.sql_text(pilot.sha(raw)),"decode('"+raw.hex()+"','hex')",f.sql_text(pilot.sha(raw)),
                      "decode('','hex')",f.sql_text(pilot.sha(response)),"'failed'",'61']) + ')')
        f.query("insert into research_runtime_pilot_amendments(id,pilot_id,protocol_raw,protocol_sha256,original_report_raw,original_report_sha256,receipt) values(" +
                ','.join([f.sql_text(aid),f.sql_text(pid),"decode('"+raw.hex()+"','hex')",f.sql_text(pilot.sha(raw)),
                          "decode('"+raw.hex()+"','hex')",f.sql_text(pilot.sha(raw)),f.sql_json({'synthetic': True, 'old_status': 'failed'})]) + ')')
        f.query("update research_runtime_pilot_calls set status='rejected' where id=" + f.sql_text(cid))
        stored = json.loads(f.query("select json_build_object('status',status,'reserved',reserved_cents,'raw',encode(request_raw,'hex'),'response',encode(response_raw,'hex')) from research_runtime_pilot_calls where id=" + f.sql_text(cid)))
        self.assertEqual(stored, {'status': 'rejected', 'reserved': 61, 'raw': raw.hex(), 'response': ''})
        f.rejects(lambda: f.query("update research_runtime_pilot_amendments set receipt='{}' where id="+f.sql_text(aid)))
        f.rejects(lambda: f.query("delete from research_runtime_pilot_amendments where id="+f.sql_text(aid)))
        for role in ('anon','authenticated'):
            self.assertEqual(f.query("select has_table_privilege(current_user,'research_runtime_pilot_amendments','SELECT,INSERT,UPDATE,DELETE')",role=role),'f')


if __name__ == '__main__':
    unittest.main()
