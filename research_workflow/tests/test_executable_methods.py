"""Development regressions, not held-out scientific or live model validation."""
import copy
import datetime as dt
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import source_arithmetic as arithmetic
import autonomy,autonomy_model,model_dispatch
import netem_iperf,network_spec,network_execution,network_package,autonomy_costs,shared_campaigns,runtime_retry,workflow

def arithmetic_fixture():
    quote='Control 72.0; treatment 97.2; difference 25.2; change 35.0%.'
    raw=workflow.canonical(dict(original_sha256='a'*64,pages=[dict(page=3,text=quote)]))
    cells=[dict(id=n,page=3,quote=quote,token_index=i,units='%' if i==3 else 'Mbit/s') for i,n in enumerate(['control','treatment','difference','percent'])]
    cfg=dict(source_id=str(uuid.uuid4()),text_artifact_id=str(uuid.uuid4()),text_sha256=workflow.digest(raw),cells=cells,
             calculations=[dict(id='gain',operation='relative-percent-change',inputs=['control','treatment'],reported_cell='percent',decimal_places=1),dict(id='delta',operation='difference',inputs=['control','treatment'],reported_cell='difference',decimal_places=1)])
    return cfg,raw

def sweep_config():
    return dict(cca='cubic',loss_levels_pct=[0,1,2,5,10],rate_mbit=100,one_way_delay_ms=20,duration_seconds=30,queue_packets=1000,mtu=1500,socket_buffer_bytes=131072,seed=27301,loss_seed=None,ping_count=3)

class Methods(unittest.TestCase):
    def test_source_arithmetic_computes_instead_of_copying_reported_value(self):
        cfg,raw=arithmetic_fixture();rows=arithmetic.evaluate(cfg,raw,'a'*64)
        self.assertEqual([r['value'] for r in rows],['35.0','25.2']);self.assertTrue(all(r['matched'] for r in rows))
        packet=json.loads(raw);packet['pages'][0]['text']=packet['pages'][0]['text'].replace('35.0%','45.0%');changed=workflow.canonical(packet)
        for c in cfg['cells']:c['quote']=packet['pages'][0]['text']
        cfg['text_sha256']=workflow.digest(changed)
        rows=arithmetic.evaluate(cfg,changed,'a'*64);self.assertEqual(rows[0]['value'],'35.0');self.assertFalse(rows[0]['matched'])
    def test_original_source_and_text_hashes_are_required(self):
        cfg,raw=arithmetic_fixture()
        for modified,source in [(raw+b' ','a'*64),(raw,'b'*64)]:
            with self.assertRaises(ValueError):arithmetic.evaluate(cfg,modified,source)
    def test_ambiguous_missing_and_zero_baseline_are_not_recorded_zero(self):
        cfg,raw=arithmetic_fixture();packet=json.loads(raw);packet['pages'][0]['text']*=2;raw=workflow.canonical(packet);cfg['text_sha256']=workflow.digest(raw)
        self.assertTrue(all(r['status']=='ambiguous' and r['value'] is None for r in arithmetic.evaluate(cfg,raw,'a'*64)))
        cfg,raw=arithmetic_fixture();cfg['cells'][0]['page']=1
        self.assertEqual(arithmetic.evaluate(cfg,raw,'a'*64)[0]['status'],'missing')
        cfg,raw=arithmetic_fixture();packet=json.loads(raw);packet['pages'][0]['text']=packet['pages'][0]['text'].replace('72.0','0.0')
        for c in cfg['cells']:c['quote']=packet['pages'][0]['text']
        raw=workflow.canonical(packet);cfg['text_sha256']=workflow.digest(raw)
        row=arithmetic.evaluate(cfg,raw,'a'*64)[0];self.assertEqual(row['status'],'invalid');self.assertIsNone(row['value'])
    def test_units_and_unregistered_operations_rejected(self):
        cfg,raw=arithmetic_fixture();cfg['cells'][1]['units']='bytes'
        with self.assertRaises(ValueError):arithmetic.validate(cfg)
        cfg,raw=arithmetic_fixture();cfg['calculations'][0]['operation']='eval'
        with self.assertRaises(ValueError):arithmetic.validate(cfg)
    def test_receiver_goodput_uses_actual_received_bytes_and_duration(self):
        raw=json.dumps(dict(end=dict(sum_received=dict(bytes=1250000,seconds=1.25,bits_per_second=8000000),sum_sent=dict(retransmits=0))))
        result=netem_iperf.parse_transfer(raw);self.assertEqual(result['throughput_mbit_s'],8);self.assertEqual(result['retransmits'],0)
        with self.assertRaises(ValueError):netem_iperf.parse_transfer(raw.replace('8000000','80000000'))
        data=json.loads(raw);data['end']['sum_received']['seconds']=0
        with self.assertRaises(ValueError):netem_iperf.parse_transfer(json.dumps(data))
    def test_sweep_bounds_and_bootstrap_are_fixed_not_user_code(self):
        cfg=sweep_config();netem_iperf.validate(cfg);self.assertEqual(netem_iperf.minimum_wall(cfg),565)
        bad=dict(cfg,loss_levels_pct=[0,True])
        with self.assertRaises(ValueError):netem_iperf.validate(bad)
        spec=dict(runner='netem-iperf-v1',config=cfg,replication=0,limits=dict(wall_seconds=600,max_output_bytes=1000000,max_network_bytes=netem_iperf.network_budget(cfg)))
        n=dict(job_id=str(uuid.uuid4()),spec=spec,spec_sha256=workflow.digest(workflow.canonical(spec)),deadline='2099-01-01T00:00:00Z')
        with patch.dict('os.environ',{'NETWORK_PACKAGE_INGEST_URL':'https://example.test/ingest'}):script=network_package.user_data(n,'https://example.test/spec',None,'a'*64)
        self.assertLess(len(script.encode()),16384);self.assertIn('iputils-ping',script);self.assertNotIn('IamInstanceProfile',script)
    def test_identical_acquisition_shares_without_losing_claim_specific_rules(self):
        p=dict(execution={'runner':'netem-iperf-v1'},configurations=[{'identity':'source-grid','details':{'test_config':sweep_config()}}],metrics=[{'name':'throughput_mbit_s','units':'Mbit/s'}],resource_limits={'wall_seconds':600},seeds={'applicable':True,'rationale':'fixed'},data_origin='new-empirical',input_versions=[{'identity':'paper','sha256':'a'*64}],hypotheses='Claim A',statistics='Criterion A')
        a=dict(protocol=p,experiment_type='new-measurement');b=copy.deepcopy(a);b['protocol'].update(hypotheses='Claim B',statistics='Criterion B')
        self.assertEqual(shared_campaigns.identity(a,'source'),shared_campaigns.identity(b,'source'))
        b['protocol']['configurations'][0]['identity']='Different human label';b['protocol']['seeds']['rationale']='Same seed, separate explanation'
        self.assertEqual(shared_campaigns.identity(a,'source'),shared_campaigns.identity(b,'source'))
        b['protocol']['configurations'][0]['details']['test_config']['rate_mbit']=200
        self.assertNotEqual(shared_campaigns.identity(a,'source')[1],shared_campaigns.identity(b,'source')[1]);self.assertEqual(a['protocol']['statistics'],'Criterion A')
    def test_retry_only_confirmed_transient_http_responses_with_remaining_time(self):
        now=dt.datetime(2026,10,26,tzinfo=dt.timezone.utc);deadline=(now+dt.timedelta(days=7)).isoformat()
        p=dict(status='failed',response_received=True,http_status=504,error_code='http-504')
        first=runtime_retry.decision(p,None,deadline,now);self.assertEqual(first['delay_seconds'],15)
        second=runtime_retry.decision(p,first,deadline,now);self.assertEqual(second['delay_seconds'],30)
        for changed in [dict(p,response_received=False),dict(p,status='unknown'),dict(p,http_status=401),dict(p,error_code='quota-exhausted')]:self.assertIsNone(runtime_retry.decision(changed,None,deadline,now))
        self.assertIsNone(runtime_retry.decision(p,None,(now+dt.timedelta(seconds=194)).isoformat(),now))
    def test_failed_http_turn_is_persisted_before_a_new_numbered_retry_is_scheduled(self):
        events=[];task=dict(id=str(uuid.uuid4()),study_id=str(uuid.uuid4()),actor_id=str(uuid.uuid4()),claim_id=str(uuid.uuid4()),attempts=0,stage='planning',context={'unchanged_design':'original'},workflow={'deadline_at':'2099-01-01T00:00:00Z'})
        def control(t,op,payload):events.append((op,payload));return dict(replayed=False)
        def failure(_):raise model_dispatch.DispatchError(dict(status='failed',provenance=dict(status='failed',response_received=True,http_status=504,error_code='http-504')))
        with patch.object(autonomy_model,'load_prompt',return_value=({'id':str(uuid.uuid4())},'Synthetic system')):
            with self.assertRaises(autonomy_model.ModelFailure) as captured:autonomy_model.call(task,{},control,failure)
        self.assertEqual([x[0] for x in events],['begin','finish']);self.assertEqual(events[-1][1]['status'],'failed');self.assertEqual(task['attempts'],1)
        self.assertEqual(workflow.digest(events[0][1]['request_raw'].encode()),events[0][1]['request_sha256'])
        with patch.object(autonomy,'step',side_effect=captured.exception),patch.object(autonomy,'advance',return_value={}) as advance:
            autonomy.process(task)
        self.assertEqual(advance.call_args.args[1],'queued');self.assertEqual(advance.call_args.kwargs['delay'],15)
        context=advance.call_args.kwargs['context'];self.assertEqual(context['unchanged_design'],'original');self.assertEqual(context['runtime_retry']['failed_turn_id'],captured.exception.turn_id)
    def test_mean_strict_increase_and_recorded_false_preserve_units(self):
        cfg,raw=arithmetic_fixture();cfg['calculations']=[dict(id='mean',operation='mean',inputs=['control','treatment'],reported_cell='difference',decimal_places=1)]
        row=arithmetic.envelope(cfg,raw,'a'*64)['observations'][0];self.assertEqual(row['value'],84.6);self.assertFalse(row['metadata']['matched'])
        cfg,raw=arithmetic_fixture();cfg['calculations']=[dict(id='trend',operation='strict-increase',inputs=['control','treatment'],reported_cell=None,decimal_places=0)]
        row=arithmetic.envelope(cfg,raw,'a'*64)['observations'][0];self.assertEqual(row['value'],1);self.assertEqual(row['units'],'boolean')
        cfg['calculations'][0]['inputs'].reverse();row=arithmetic.envelope(cfg,raw,'a'*64)['observations'][0]
        self.assertEqual(row['value'],0);self.assertEqual(row['status'],'recorded');self.assertFalse(row['metadata']['matched'])
    def test_unicode_minus_grouped_numbers_and_rounding_context(self):
        from decimal import localcontext,ROUND_UP
        self.assertEqual(arithmetic.decimal_token('−1,000.5'),-1000.5)
        self.assertEqual(arithmetic.TOKEN.findall('A −1,000.5; B 2,000.0.'),['−1,000.5','2,000.0'])
        cfg,raw=arithmetic_fixture();packet=json.loads(raw);packet['pages'][0]['text']=packet['pages'][0]['text'].replace('72.0','0.0').replace('97.2','0.1').replace('25.2','0.0')
        for cell in cfg['cells']:cell['quote']=packet['pages'][0]['text']
        cfg['calculations']=[dict(id='mean',operation='mean',inputs=['control','treatment'],reported_cell='difference',decimal_places=1)]
        raw=workflow.canonical(packet);cfg['text_sha256']=workflow.digest(raw)
        with localcontext() as ctx:
            ctx.rounding=ROUND_UP
            result=arithmetic.evaluate(cfg,raw,'a'*64)[0]
        self.assertEqual(result['value'],'0.0');self.assertTrue(result['matched'])

if __name__=='__main__':unittest.main()
