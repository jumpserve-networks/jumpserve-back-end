"""Predeclared exact finite controls of byte-preserved author functions."""
import ast,copy,datetime as dt,gzip,hashlib,json,logging,resource,subprocess,sys,time
from pathlib import Path
from corrected import preserve_observations,duration_summary
ROOT=Path(__file__).resolve().parent
AUTHOR=ROOT/'author/srv/script_backup/crontrol-hosts/backend.dns-mtu.measurement.network/opt/msmt/msmt/scripts'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def fixture(answer,status='NOERROR',duration=1):return dict(name='fixture.example',results={'NS':dict(data={'answers':[dict(type='NS',answer=answer,ttl=60)]},status=status,duration=duration)})
def main():
 target=ROOT/'evidence/controls-v1.json'
 if target.exists():raise RuntimeError('Preserve prior controls before creating any fixtures')
 protocol=ROOT/'protocols/ipv6-dns-controls-v1.json';assert sha(protocol)==json.loads((protocol.with_suffix('.lock.json')).read_text())['sha256']
 source=AUTHOR/'aggregate_dns_data.py';tree=ast.parse(source.read_text());functions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ['get_result_hash','get_result_stats','parse_data','norm_len','print_percent']]
 env=dict(json=json,gzip=gzip,hashlib=hashlib,time=time,resource=resource,sys=sys,logger=logging.getLogger('ipv6_finite_controls'),LOGLEVEL='error',FLEN={},PARSE_MAX=-1,FMAX=0,usage=0,maxuse=0)
 exec(compile(ast.Module(body=functions,type_ignores=[]),str(source),'exec'),env)
 output=[]
 for case,records in [('distinct-answers',[fixture('ns1.example'),fixture('ns2.example',duration=3)]),('same-answer-different-status',[fixture('ns1.example'),fixture('ns1.example','SERVFAIL',3)])]:
  path=ROOT/f'raw/controls/{case}/case/data.jsonl';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(''.join(json.dumps(r)+'\n' for r in records));env['FLEN'][str(path)]=len(records)
  result=env['parse_data']({},str(path))['fixture.example']['case'];corrected=preserve_observations(copy.deepcopy(records))
  output.append(dict(id=case,protocol_id='ipv6-dns-controls-v1',stage='correctness',status='complete',reason=None,raw_sha256=sha(path),source_sha256=sha(source),original=result,corrected=corrected,expected_observations=2,original_retained_observations=sum(g['count'] for g in result),corrected_retained_observations=sum(g['count'] for g in corrected),finding='Distinct answer group replaces earlier group' if case=='distinct-answers' else 'Status-only difference shares hash; first status retained',limitation='Synthetic finite fixture; published generating revision and real occurrence frequencies unknown.'))
 for case,durations in [('duration-average',[1,3]),('empty-duration',[])]:
  folder=ROOT/f'raw/controls/{case}';inp=folder/'input';out=folder/'output';inp.mkdir(parents=True,exist_ok=True);out.mkdir(exist_ok=True)
  data={'fixture.example':{'fixture-case':[dict(count=2,result_hash='fixture',stats=dict(dnssec=False,edns0=False,status='NOERROR',duration=durations))]}}
  with gzip.open(inp/'fixture.json.gz','wt') as f:json.dump(data,f)
  run=subprocess.run([sys.executable,str(AUTHOR/'create_stats.py'),'2025-01-01',str(inp),str(out)],capture_output=True,text=True)
  (folder/'stdout.txt').write_text(run.stdout);(folder/'stderr.txt').write_text(run.stderr)
  result=json.loads(gzip.open(out/'all_psl_domains.json.gz','rt').read())['all']['fixture-case'] if run.returncode==0 else None
  output.append(dict(id=case,protocol_id='ipv6-dns-controls-v1',stage='correctness',status='complete' if run.returncode==0 else 'failed',reason=None if run.returncode==0 else 'Author max([]) raises ValueError; failed run retained',raw_sha256=sha(inp/'fixture.json.gz'),stdout_sha256=sha(folder/'stdout.txt'),stderr_sha256=sha(folder/'stderr.txt'),source_sha256=sha(AUTHOR/'create_stats.py'),exit_code=run.returncode,original=result,corrected=duration_summary(durations),finding='duration_avg3 seconds instead of arithmetic mean2 seconds' if result else 'Empty duration is invalid in author implementation',limitation='Not evidence that these bytes generated published latency; no historical latency recalculation attempted.'))
 report=dict(id='ipv6-dns-controls-v1',executed_at=dt.datetime.now(dt.timezone.utc).isoformat(),protocol_sha256=sha(protocol),correction_sha256=sha(ROOT/'corrected.py'),reviewer={'identity':'Codex primary assistant','type':'AI','independence':'Implementer'},runs=output,checks={'distinct_answers_preserved':output[0]['corrected_retained_observations']==2,'original_distinct_loss_demonstrated':output[0]['original_retained_observations']==1,'mean_control':output[2]['corrected']['mean']==2 and output[2]['original']['duration_avg']==3,'missing_preserved':output[3]['corrected']['mean'] is None})
 target.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report['checks']))
if __name__=='__main__':main()
