"""Latest lab subscription queue/usage status. Read-only local SQL, no AI calls."""
import json
from pathlib import Path
import subscription as sub


def latest():
    candidates=[path.parent for path in (sub.ROOT / 'artifacts').glob('v*/frozen.json')
                if path.parent.name[1:].isdigit() and int(path.parent.name[1:])>=7]
    if not candidates:raise ValueError('No frozen subscription campaign')
    return max(candidates,key=lambda path:int(path.name[1:]))


def status():
    base=latest();packet=json.loads((base / 'frozen.json').read_bytes());pid=sub.lab.database.sql_text(packet['id'])
    result=dict(version='subscription-status-v1',campaign=base.name,model=sub.MODEL,billing='chatgpt-subscription',
        status='unknown',calls=None,usage=None,paid_api_fallback=False,allowance_remaining=None,allowance_reset_at=None,
        subscription_allocation_usd=None,measured_charges_usd=None,usage_link='https://chatgpt.com/settings/usage')
    try:
        query=lambda sql:sub.lab.database.query(sql,database=packet['coordinator_database'])
        state=json.loads(query("select json_build_object('status',status,'started_at',started_at,'ended_at',ended_at) from research_subscription_pilots where id="+pid))
        calls=json.loads(query("select coalesce(json_agg(t),'[]') from (select number,status,usage,reserved_cents from research_subscription_calls where pilot_id="+pid+' order by number)t'))
        task=json.loads(query("select json_build_object('status',status,'stage',stage,'attempts',attempts,'label',label) from research_autonomy_tasks where id="+sub.lab.database.sql_text(packet['arm']['task_id'])))
        study=sub.lab.database.sql_text(packet['arm']['study_id'])
        coverage=json.loads(query("select json_build_object('measurements',(select count(*) from research_measurements where study_id="+study+
            "),'recorded_measurements',(select count(*) from research_measurements where study_id="+study+" and status='recorded'),"+
            "'complete_runs',(select count(*) from research_runs where study_id="+study+" and status='complete'))"))
        if state['status']=='running':
            state['prior_followup_ended_at']=state['ended_at'];state['ended_at']=None
        totals={}
        for key in ('inputTokens','outputTokens','totalTokens'):
            values=[(call.get('usage') or {}).get(key) for call in calls]
            totals[key]=dict(known_sum=sum(value for value in values if type(value) is int),missing_calls=sum(type(value) is not int for value in values))
        result.update(state,calls=len(calls),call_statuses={key:sum(call['status']==key for call in calls) for key in sorted({call['status'] for call in calls})},
            task=task,usage=totals,coverage=coverage,planned_cells=1152,source='Current isolated local PostgreSQL',observed_at=sub.lab.stamp(),
            aggregate_committed_cents=packet['prior_committed_cents']+1000+sum(call['reserved_cents'] for call in calls))
    except Exception as error:
        result.update(reason='Local database unavailable; counts remain unknown.',error_type=type(error).__name__,source='No current observation')
    return result


if __name__=='__main__':print(json.dumps(status()))
