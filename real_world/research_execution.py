"""Private IAM invocation adapter; public API requests retain Google verification."""
import datetime as dt
import math
import time
import uuid
import database
import store
from reports import load_report
from config import public_job

def envelope(event):
    if event.get('requestContext') or event.get('source')!='jumpserve.network-execution': return False
    if set(event)!={'source','job_id','action'} or event['action'] not in ('launch','status','cancel'):
        raise ValueError('Invalid saved networking envelope.')
    uuid.UUID(event['job_id'])
    return True

def handle(event,launch):
    if database.base_url()!='https://regphejnlvfpyokpniny.supabase.co': raise ValueError('Networking persistence target differs.')
    n=database.rpc('research_network_launch',dict(p_job=event['job_id'],p_runner='real-world-iperf-v1',p_claim=event['action']=='launch'))
    claimed=bool(n and event['action']=='launch')
    if n is None: n=database.rpc('research_network_launch',dict(p_job=event['job_id'],p_runner='real-world-iperf-v1',p_claim=False))
    if not n: raise ValueError('Saved networking invocation required.')
    if claimed: launch(dict(config=n['spec']['config'],request_id=event['job_id']),n['owner_id'])
    job_id=str(uuid.uuid5(uuid.NAMESPACE_URL,n['owner_id']+':'+event['job_id']))
    job=store.load(job_id)
    deadline=dt.datetime.fromisoformat(n['deadline'].replace('Z','+00:00')).timestamp()
    if job and (event['action']=='cancel' or time.time()>=deadline) and job['status'] not in ('completed','failed','cancelled'):
        store.cancel(job_id)
    status=job['status'] if job else 'failed' if time.time()>=deadline else 'launch-unconfirmed'
    result=dict(status=status,external_job_id=job_id,started_at=dt.datetime.fromtimestamp(job['created_at'],dt.timezone.utc).isoformat() if job else n['started_at'],
        ended_at=dt.datetime.fromtimestamp(job['updated_at'],dt.timezone.utc).isoformat() if job and status in ('completed','failed','cancelled') else None,
        reason=job.get('error') if job else 'Launch acknowledgement unavailable.',resources=dict(nodes=public_job(job).get('nodes',[])) if job else {})
    if job and status in ('completed','failed','cancelled'):
        report=load_report(job,summary_only=True)
        output=[]
        mapping={'throughput_mbit_s':lambda r:r.get('mean_mbit_per_second'),'received_bytes':lambda r:r.get('received_bytes'),'duration_seconds':lambda r:r.get('duration_seconds'),
            'rtt_median_ms':lambda r:r.get('rtt',{}).get('median'),'retransmits':lambda r:r.get('retransmits')}
        for receiver in report['receivers']:
            for metric in n['spec']['metrics']:
                value=mapping[metric['name']](receiver)
                valid=isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)
                output.append(dict(observation_key=receiver['name'],metric=metric['name'],units=metric['units'],value=value if valid else None,status='recorded' if valid else 'missing',
                    reason=None if valid else 'Metric unavailable in the original receiver report.',metadata=dict(placement=receiver['placement'],original_job_id=job_id)))
        result['output']=dict(version='network-observations-v1',observations=output,provenance=dict(module='congestion-control-real-world',analysis_version=report['analysis_version'],
            sources=report.get('sources',[]),warnings=report.get('warnings',[]),limitations='Per-receiver summaries; original traces/artifacts remain in the real-world measurement repository. No new statistical intervals.'),usage=dict(measured_charges_usd=None))
    return result
