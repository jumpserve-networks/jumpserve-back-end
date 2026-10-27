"""Share identical acquisitions, retaining separate claim protocols and reviews.

This is identity-based reuse, not inference that observations are independent.
Comparison rules remain in each claim's frozen protocol. A changed acquisition
or source produces a new campaign. SQL arbitrates concurrent first writers.
"""
import copy
from workflow import canonical,digest
import store

VERSION='research-shared-acquisition-v1'

def identity(proposal,source_id):
    p=proposal['protocol']
    acquisition={k:copy.deepcopy(p[k]) for k in ('execution','resource_limits','data_origin')}
    # Human labels and prose do not change an executable acquisition. Preserve
    # every actual configuration/seed/input byte identity and metric bound.
    # Do not deduplicate repeated configurations: multiplicity is coverage.
    acquisition['configurations']=sorted([dict(test_config=copy.deepcopy(c['details']['test_config'])) for c in p['configurations']],key=canonical)
    acquisition['metrics']=sorted(copy.deepcopy(p['metrics']),key=lambda m:m['name'])
    acquisition['seeds']=dict(applicable=p['seeds']['applicable'])
    acquisition['input_sha256']=sorted({x['sha256'] for x in p['input_versions'] if not x['identity'].startswith('network-execution-v1:')})
    acquisition.update(version=VERSION,source_id=source_id,experiment_type=proposal['experiment_type'])
    return acquisition,digest(canonical(acquisition))

def register(task,proposal,records,campaign_id,protocol_id):
    source_id=task['context'].get('source_id')
    if not source_id:
        # Registered archived plans may predate extracted-text task bindings.
        # Their authoritative claim still identifies the exact source.
        claims=store.rows('claims',dict(study_id='eq.'+task['study_id'],id='eq.'+task['claim_id'],select='id,source_id',limit=2))
        if len(claims)!=1:raise ValueError('Shared acquisition requires the original claim/source identity.')
        source_id=claims[0]['source_id']
    acquisition,sha=identity(proposal,source_id)
    return store.rpc('research_shared_campaign_bind',dict(p_study=task['study_id'],p_actor=task['actor_id'],p_task=task['id'],p_claim=task['claim_id'],
        p_sha=sha,p_acquisition=acquisition,p_campaign=campaign_id,p_protocol=protocol_id,p_records=records))

def available(task,fetch=None):
    # Bounded catalog; original complete protocols remain readable by identity.
    items=fetch('shared_campaigns',order='created_at.desc',limit=8) if fetch else store.rows('shared_campaigns',dict(study_id='eq.'+task['study_id'],order='created_at.desc',limit=8))
    result=[]; used=0
    for item in items:
        if item['acquisition'].get('source_id')!=task['context'].get('source_id'): continue
        row=dict(campaign_id=item['campaign_id'],acquisition_sha256=item['acquisition_sha256'],read_request=dict(kind='acquisition',id=item['id'],cursor=0))
        raw=canonical(item['acquisition'])
        if len(raw)<=5000 and used+len(raw)<=12000: row.update(acquisition=item['acquisition'],representation='complete-inline');used+=len(raw)
        else:row.update(representation='retained-acquisition-reference',byte_count=len(raw),limitation='Full acquisition retained. Inspect bounded fragments; omitted inline fields are not missing inputs.')
        result.append(row)
    return result
