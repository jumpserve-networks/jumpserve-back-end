"""Append-only local subscription-pilot evidence export; no model or auth calls."""
import hashlib
import json

import subscription as sub
import subscription_status


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def export():
    sub.BASE=subscription_status.latest()
    frozen=json.loads((sub.BASE / 'frozen.json').read_bytes())
    sub.verify_frozen(frozen)
    directory=sub.ROOT / 'artifacts/subscription-reports' / sub.lab.dt.datetime.now(sub.lab.dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    directory.mkdir(parents=True,exist_ok=False)
    originals=0
    for name,expected in frozen['files'].items():
        if digest(sub.BASE / 'inputs' / name)!=expected:
            raise ValueError('Frozen input snapshot hash mismatch: '+name)
        originals+=1
    for blob in frozen['arm']['storage'].values():
        if digest(sub.BASE / blob['path'])!=blob['sha256']:
            raise ValueError('Original local Storage fixture hash mismatch')
        originals+=1
    protocol_sql=json.loads(sub.lab.database.query("select json_build_object('raw',encode(protocol_raw,'hex'),'hash',protocol_sha256,'status',status) from research_subscription_pilots where id="+
        sub.lab.database.sql_text(frozen['id']),database=frozen['coordinator_database']))
    if bytes.fromhex(protocol_sql['raw'])!=sub.PROTOCOL.read_bytes() or protocol_sql['hash']!=digest(sub.PROTOCOL):
        raise ValueError('Original protocol hash mismatch after SQL retrieval')
    recoveries=json.loads(sub.lab.database.query("select coalesce(json_agg(t),'[]') from (select id,protocol_sha256,encode(protocol_raw,'hex') protocol_hex,original_report_sha256,encode(original_report_raw,'hex') report_hex from research_subscription_recoveries where pilot_id="+
        sub.lab.database.sql_text(frozen['id'])+')t',database=frozen['coordinator_database']))
    for receipt in recoveries:
        if sub.lab.sha(bytes.fromhex(receipt['protocol_hex']))!=receipt['protocol_sha256'] or sub.lab.sha(bytes.fromhex(receipt['report_hex']))!=receipt['original_report_sha256']:
            raise ValueError('Stored original amendment bytes differ after retrieval')
    counts=json.loads(sub.lab.database.query("select json_build_object('admitted',count(*),'complete',count(*) filter(where status='complete')) from research_subscription_calls where pilot_id="+
        sub.lab.database.sql_text(frozen['id']),database=frozen['coordinator_database']))
    manifest=[]
    for base in [sub.ROOT / 'artifacts' / ('v'+str(number)) for number in range(5,int(sub.BASE.name[1:])+1)]:
        for path in sorted(base.rglob('*')):
            if path.is_file():
                manifest.append(dict(path=str(path.relative_to(sub.ROOT)),bytes=path.stat().st_size,sha256=digest(path)))
    sub.lab.save(directory / 'raw-manifest.json',dict(files=manifest,logical_bytes=sum(row['bytes'] for row in manifest),
        credential_directory_included=False,scope='Private local subscription preparations/campaigns and versioned follow-ups. Hash manifest only; original bytes remain private.'))
    sources=[]
    for path in sorted((sub.ROOT / 'artifacts/documentation/v5').glob('*.md')):
        url=('https://learn.chatgpt.com/docs/sign-in-with-chatgpt.md' if path.name=='sign-in-with-chatgpt.md' else
             'https://developers.openai.com/siwc/token-sharing-open-source/'+path.name)
        sources.append(dict(citation=path.stem.replace('-',' ').title(),role='provider documentation',url=url,
            retrieved_version='Unversioned Markdown retrieved 2026-10-20; exact original bytes identified by SHA-256',
            sha256=digest(path),bytes=path.stat().st_size,access_status='retrieved',review_status='partial review',
            attempts=[dict(method='curl -fsSL --max-time 30',status='successful',date='2026-10-20',exact_timestamp=None)],
            examined='Relevant main-text subscription permissions, model discovery, request requirements, completion/errors and usage controls.',
            not_examined='Rendered illustrations and linked documentation outside the stated implementation scope; no full literature review is claimed.'))
    sub.lab.save(directory / 'sources.json',dict(sources=sources,
        complete_review_definition='Entire resource main text, appendices and applicable figures/tables examined and findings recorded; byte retrieval alone is insufficient.',
        paper_scope='Original paper and bibliography records remain in the earlier archived study; this provider follow-up does not repeat that literature review.'))
    gate_path=sub.ROOT / frozen['software_gate']['path']
    gate=json.loads(gate_path.read_bytes())
    current_protocol=sub.ROOT / ('protocol-'+sub.BASE.name+'.json')
    result=dict(version='subscription-pilot-evidence-v2',recorded_at=sub.lab.stamp(),protocol_id=json.loads(current_protocol.read_bytes())['id'],
        protocol_sha256=digest(current_protocol),original_protocol_sha256=digest(sub.PROTOCOL),status=subscription_status.status(),local_sql_status=protocol_sql['status'],provider_attempts=counts,
        software_gate=dict(passed=gate['passed'],path=str(gate_path.relative_to(sub.ROOT)),sha256=digest(gate_path)),
        frozen_original_files_verified=originals,original_protocol_verified_after_sql_retrieval=True,
        amendment_original_byte_pairs_verified=len(recoveries),
        raw_manifest_sha256=digest(directory / 'raw-manifest.json'),prior_conservative_commitment_cents=frozen['prior_committed_cents'],
        prepared_overhead_reserved_cents=1000,measured_charges_usd=None,subscription_allocation_usd=None,
        local_compute_charges_usd=None,local_storage_charges_usd=None,production_mutations=0,
        review=dict(identity='Codex primary implementer',type='AI',independent=False,human=False),
        conditional_gaps=['Real ChatGPT SIWC consent and account model catalog','Actual subscription inference and usage receipts',
            'New-model controller trajectory and numerical coverage','Remote Supabase persistence','Public website integration and isolation'],
        interpretation='Software controls verified locally; scientific and live-provider feasibility are not established by synthetic tests. Missing costs and allowance are unknown, not zero.')
    if (sub.BASE / 'gate.json').exists():
        latest_gate=json.loads((sub.BASE / 'gate.json').read_bytes())
        result['followup_software_gate']=dict(passed=latest_gate['passed'],sha256=digest(sub.BASE / 'gate.json'),
            output_sha256=digest(sub.BASE / 'gate-stderr.bin'),kind='Exposed development regressions and retained-answer reanalysis, not independent held-out validation.')
    if (sub.BASE / 'report.json').exists():
        actual=json.loads((sub.BASE / 'report.json').read_bytes())
        result['campaign_report_sha256']=digest(sub.BASE / 'report.json')
        if actual.get('catalog'):
            result['conditional_gaps'].remove('Real ChatGPT SIWC consent and account model catalog')
        if counts['complete']:
            result['conditional_gaps'].remove('Actual subscription inference and usage receipts')
        result['coverage']=dict(recorded_cells=actual.get('covered_cells'),planned_cells=actual.get('planned_cells'),
            decimal_oracle=actual.get('decimal_oracle'),controller_claim_label=actual.get('trajectory',{}).get('label'),
            controller_terminal_status=actual.get('trajectory',{}).get('task_status'),
            whole_workflow_feasibility_pass=actual.get('passed'),data_origin='archived-reanalysis')
        if actual.get('covered_cells')==1152 and actual.get('decimal_oracle'):
            result['conditional_gaps'].remove('New-model controller trajectory and numerical coverage')
            result['conditional_gaps'].append('Final controller assessment was not reached before the unchanged 25-attempt cap; its inconclusive label is retained.')
        result['conditional_gaps']+=['One failed request has unavailable usage; invoice charges and subscription allocation remain unknown.',
            'Pinned runtime/lockfile and listed inputs are hashed; the complete installed dependency tree was not independently byte-verified.',
            'No held-out prompt-injection or independent human review; no new Internet measurements or full-paper validation.']
        result['interpretation']='Subscription inference and six archived numerical checks executed. All 1152 recorded cells match their frozen criteria; the controller stopped at 25 attempts before final assessment. Numerical agreement is separate from workflow completion and broader scientific validity.'
    sub.lab.save(directory / 'validation.json',result)
    print(json.dumps(dict(report=str(directory / 'validation.json'),state=result['status']['status'],
        verified_original_files=originals,manifest_files=len(manifest),provider_attempts=counts)))


if __name__=='__main__':
    export()
