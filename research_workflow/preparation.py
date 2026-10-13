"""Source-grounded plans -> immutable protocols -> resumable campaign queues.

Plans are data, never submitted code. Registered plans preserve their reviewed
versions. Uploaded plans retain their producer's review declarations; importing
them does not establish independent review or successful experiments.
"""
import copy
import json
import re
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit
import scheduler
import store
from workflow import canonical, digest, identifier, protocol_record, public_url, text, validate_record

VERSION = 'research-preparation-v1'
MAX_PLAN_BYTES = 1_500_000
MAX_UPLOADED_BYTES = 250_000
ALLOWED_RECORDS = {'sources', 'claims', 'configurations', 'assessments', 'gaps'}
REF_FIELDS = {'id', 'source_id', 'claim_id', 'campaign_id', 'protocol_id', 'configuration_id', 'assessment_id', 'supersedes_id', 'followup_of', 'run_id', 'published_id'}
PLAN_DIRECTORY = Path(__file__).parent / 'plans'
JSON_STRING_PAIR = re.compile(r'("(?:\\.|[^"\\])*")(\s*:\s*)("(?:\\.|[^"\\])*")')

def rebound_id(value, mapping):
    if not isinstance(value, str): return value
    try: key = identifier(value)
    except ValueError: return value
    return mapping.get(key, value)

def rebind_input(original, mapping):
    # Parse separately to validate JSON. Replace identifier string tokens only;
    # never round-trip numeric tokens through binary floats (underflow, large
    # integers and lexical precision must remain visible to the actual runner).
    def replace(match):
        key, separator, token = match.groups()
        field = json.loads(key); value = json.loads(token)
        replacement = rebound_id(value, mapping)
        if field in REF_FIELDS and replacement != value:
            return key + separator + json.dumps(replacement)
        return match.group(0)
    return JSON_STRING_PAIR.sub(replace, original)

def url_identity(value):
    public_url(value)
    parsed = urlsplit(value)
    return (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip('/'), parsed.query)

def parse(raw, maximum=MAX_PLAN_BYTES):
    if len(raw) > maximum: raise ValueError('Preparation plan exceeds the declared byte budget; split versioned plans rather than truncate.')
    plan = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite plan values are invalid.')))
    if not isinstance(plan, dict) or set(plan) != {'schema_version', 'plan_id', 'title', 'paper_urls', 'provenance', 'records', 'campaigns'} or plan['schema_version'] != 1:
        raise ValueError('Use a schema-version-1 preparation plan with the declared fields.')
    if not isinstance(plan['plan_id'], str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}', plan['plan_id']): raise ValueError('Invalid versioned plan identifier.')
    text(plan['title'], 'Plan title', 300)
    if not isinstance(plan['paper_urls'], list) or not 1 <= len(plan['paper_urls']) <= 10: raise ValueError('Declare 1–10 exact paper URL identities.')
    for value in plan['paper_urls']: url_identity(value)
    if not isinstance(plan['provenance'], dict): raise ValueError('Plan provenance is required.')
    for key in ('producer', 'source_version', 'prior_exposure', 'limitations'): text(plan['provenance'].get(key), 'provenance.' + key)
    if not isinstance(plan['records'], list) or not 1 <= len(plan['records']) <= 500: raise ValueError('Plan needs 1–500 source-grounded records.')
    seen = set()
    for entry in plan['records']:
        if not isinstance(entry, dict) or set(entry) != {'kind', 'record'} or entry['kind'] not in ALLOWED_RECORDS: raise ValueError('Plans contain source, claim, configuration, prior-assessment and gap records only.')
        row = validate_record(entry['kind'], entry['record'])
        if row['id'] in seen: raise ValueError('Plan record identifiers must be unique across kinds.')
        seen.add(row['id'])
    if not isinstance(plan['campaigns'], list) or not 1 <= len(plan['campaigns']) <= 100: raise ValueError('Plan needs 1–100 campaigns.')
    keys = set()
    for campaign in plan['campaigns']:
        required = {'key', 'campaign', 'protocol', 'claim_ids', 'execution_mode', 'raw_input', 'dependencies', 'priority', 'rationale', 'exclusive_resources'}
        if not isinstance(campaign, dict) or set(campaign) != required: raise ValueError('Campaign preparation fields are incomplete or unsupported.')
        key = campaign['key']
        if not isinstance(key, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}', key) or key in keys: raise ValueError('Campaign keys must be unique bounded names.')
        keys.add(key)
    ordered, visiting, visited = [], set(), set()
    indexed = {c['key']: c for c in plan['campaigns']}
    def visit(key):
        if key in visiting: raise ValueError('Preparation dependencies contain a cycle.')
        if key in visited: return
        visiting.add(key)
        dependencies = indexed[key]['dependencies']
        if not isinstance(dependencies, list) or len(dependencies) > 32: raise ValueError('Preparation dependencies exceed the bounded graph.')
        for dependency in dependencies:
            if not isinstance(dependency, dict) or set(dependency) != {'key', 'requirement'} or dependency['key'] not in indexed or dependency['requirement'] not in ('complete-run', 'reviewed-evidence'):
                raise ValueError('Dependencies need an existing campaign key and a declared criterion.')
            visit(dependency['key'])
        visiting.remove(key); visited.add(key); ordered.append(indexed[key])
    for key in indexed: visit(key)
    plan['campaigns'] = ordered
    return plan

def registered(study):
    for path in sorted(PLAN_DIRECTORY.glob('*.json')):
        raw = path.read_bytes(); plan = parse(raw)
        if url_identity(study['paper_url']) in [url_identity(url) for url in plan['paper_urls']]: return plan, raw
    return None, None

def compile_plan(plan, raw, study):
    if url_identity(study['paper_url']) not in [url_identity(url) for url in plan['paper_urls']]: raise ValueError('This preparation plan identifies a different paper.')
    study_id = identifier(study['id']); namespace = uuid.UUID(study_id); sha = digest(raw)
    prepared_id = str(uuid.uuid5(namespace, 'prepared:' + sha))
    def uid(name): return str(uuid.uuid5(uuid.UUID(prepared_id), name))
    mapping = {identifier(entry['record']['id']): uid('record:' + identifier(entry['record']['id'])) for entry in plan['records']}
    def remap(value, field=None):
        if isinstance(value, dict): return {key: remap(item, key) for key, item in value.items()}
        if isinstance(value, list): return [remap(item, field) for item in value]
        return rebound_id(value, mapping) if field in REF_FIELDS else value
    entries = [dict(kind=e['kind'], record=validate_record(e['kind'], remap(e['record']))) for e in plan['records']]
    source_ids = {e['record']['id'] for e in entries if e['kind'] == 'sources'}
    paper_identities = {url_identity(url) for url in plan['paper_urls']}
    if not any(url_identity(url) in paper_identities for e in entries if e['kind'] == 'sources' for url in (e['record']['source_url'], e['record']['retrieved_url']) if url): raise ValueError('Inventory the exact main paper as a source; URL aliases must identify the same reviewed version.')
    claims = {e['record']['id'] for e in entries if e['kind'] == 'claims'}
    if not claims: raise ValueError('At least one genuinely sourced claim is required; preparation tasks are not invented paper claims.')
    for entry in entries:
        if entry['kind'] == 'claims' and entry['record']['source_id'] not in source_ids: raise ValueError('Every claim must reference a source in this plan.')
        if entry['kind'] in ('assessments', 'gaps') and entry['record']['claim_id'] not in claims: raise ValueError('Plan assessment or gap references an unavailable claim.')
    jobs, covered = [], set()
    for item in plan['campaigns']:
        claim_ids = item['claim_ids']
        if not isinstance(claim_ids, list) or not claim_ids: raise ValueError('Every campaign needs applicable claim identities from this plan.')
        claim_ids = [identifier(key) for key in claim_ids]
        if len(claim_ids) != len(set(claim_ids)) or any(mapping.get(key) not in claims for key in claim_ids): raise ValueError('Every campaign needs unique applicable claim identities from this plan.')
        campaign_id = uid('campaign:' + item['key']); protocol_id = uid('protocol:' + item['key']); job_id = uid('job:' + item['key'])
        document = remap(copy.deepcopy(item['protocol'])); input_text = None
        if not isinstance(document, dict): raise ValueError('Campaign protocol must be an inspected design document.')
        if item['execution_mode'] == 'automatic':
            if not isinstance(item['raw_input'], str): raise ValueError('Automatic plans require original input text.')
            original_input = item['raw_input'].encode()
            if len(original_input) > scheduler.LIMITS['input_bytes']: raise ValueError('Prepared numerical input exceeds 256 KB.')
            parsed_input = json.loads(original_input, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite input values are invalid.')))
            # Only identifiers are rebound. The original manifest and input text
            # remain private originals; this derived adapter file is hashed anew.
            input_text = rebind_input(item['raw_input'], mapping)
            input_sha = digest(input_text.encode())
            found = False
            for version in document.get('input_versions', []):
                if version.get('sha256') == digest(original_input): version['sha256'] = input_sha; found = True
            if not found: raise ValueError('Automatic protocol must identify the exact manifest input bytes before rebinding source IDs.')
            limits = document['resource_limits']
            if limits['max_observations'] > 1000 or limits['wall_seconds'] > 10 or len(input_text.encode()) > min(limits['max_input_bytes'], scheduler.LIMITS['input_bytes']): raise ValueError('Prepared campaign exceeds bounded numerical resources.')
            if not isinstance(parsed_input, dict) or set(parsed_input) != {'published', 'observed'}: raise ValueError('Prepared automatic input must contain published and observed arrays.')
            if not isinstance(parsed_input['published'], list) or not isinstance(parsed_input['observed'], list): raise ValueError('Published and observed inputs must be explicit arrays.')
            if not 1 <= len(parsed_input['published']) <= limits['max_observations'] or len(parsed_input['observed']) > limits['max_observations']: raise ValueError('Input observation coverage exceeds its frozen budget.')
            for row in parsed_input['published']:
                if not isinstance(row, dict) or rebound_id(row.get('source_id'), mapping) not in source_ids: raise ValueError('Published input references an unavailable source.')
        elif item['execution_mode'] != 'manual' or item['raw_input'] is not None: raise ValueError('Manual preparation has no fabricated numerical input.')
        document['preparation'] = dict(version=VERSION, original_manifest_sha256=sha, original_manifest_plan_id=plan['plan_id'], input_transformation='Source identifiers rebound to this study; numerical values, units and observation identities preserved.' if input_text is not None else 'No experiment executed; operator preparation/review task only.')
        protocol = protocol_record(document, protocol_id, registration_kind='retrospective-import' if item['campaign']['stage'] == 'retrospective-import' else 'prospective')
        campaign = validate_record('campaigns', dict(item['campaign'], id=campaign_id, protocol_id=protocol_id))
        if item['execution_mode'] == 'automatic' and (campaign['adapter'] != 'matched-numeric-v1' or campaign['experiment_type'] not in ('reanalysis', 'independent-check')): raise ValueError('Automatic preparation uses a registered numerical adapter only.')
        entries += [dict(kind='protocols', record=protocol), dict(kind='campaigns', record=campaign)]
        for old in claim_ids:
            claim_id = mapping[old]; covered.add(claim_id)
            entries.append(dict(kind='claim_checks', record=validate_record('claim_checks', dict(id=uid('check:' + item['key'] + ':' + old), claim_id=claim_id, campaign_id=campaign_id, method='matched-numeric-v1' if input_text is not None else 'Source-grounded operator follow-up preparation; full domain experiment protocol remains required.', applicability='applicable', rationale=item['rationale']))))
        dependencies = [dict(job_id=uid('job:' + d['key']), requirement=d['requirement']) for d in item['dependencies']]
        body = dict(request_id=job_id, campaign_id=campaign_id, execution_mode=item['execution_mode'], priority=item['priority'], dependencies=dependencies, exclusive_resources=item['exclusive_resources'], raw_input=input_text, rationale=item['rationale'], followup_of=None)
        scheduler.definition(body)
        jobs.append(dict(key=item['key'], id=job_id, title=campaign['title'], campaign_id=campaign_id, execution_mode=item['execution_mode'], request=body))
    if covered != claims: raise ValueError('Every imported claim needs a planned applicable check; uncovered claims cannot silently vanish.')
    return dict(id=prepared_id, study_id=study_id, plan_id=plan['plan_id'], manifest_sha256=sha, records=entries, jobs=jobs, provenance=plan['provenance'])

def summary(prepared, jobs):
    indexed = {j['id']: j for j in jobs}; public_jobs = []
    for job in prepared['jobs']:
        prior = indexed.get(job['id'])
        public_jobs.append(dict(id=job['id'], campaign_id=job['campaign_id'], title=job['title'], execution_mode=job['execution_mode'], status=prior['status'] if prior else 'not-queued'))
    queued = sum(job['status'] != 'not-queued' for job in public_jobs)
    return dict(id=prepared['id'], plan_id=prepared['plan_id'], manifest_sha256=prepared['manifest_sha256'], jobs=public_jobs, planned_jobs=len(public_jobs), queued_jobs=queued, automatic_jobs=sum(j['execution_mode'] == 'automatic' for j in public_jobs), manual_jobs=sum(j['execution_mode'] == 'manual' for j in public_jobs), status='queued' if queued == len(public_jobs) else 'partially-queued' if queued else 'prepared', provenance=prepared['provenance'])

def compiled(study_id, row):
    raw = store.artifact_bytes(study_id, row['compiled_artifact_id'], maximum=4_000_000)
    value = json.loads(raw)
    if value.get('id') != row['id'] or value.get('study_id') != study_id or value.get('manifest_sha256') != row['manifest_sha256']: raise store.StoreError('Prepared plan identity differs from its retained original.')
    return value

def status(study):
    plan, raw = registered(study)
    rows = store.rows('prepared_plans', {'study_id': 'eq.' + study['id'], 'limit': 101})
    if len(rows) > 100: raise store.StoreError('Preparation history exceeds interactive coverage; use an operator export.')
    jobs = store.rows('queue_jobs', {'study_id': 'eq.' + study['id']})
    return dict(version=VERSION, available_plan=dict(plan_id=plan['plan_id'], title=plan['title'], manifest_sha256=digest(raw), claims=sum(e['kind'] == 'claims' for e in plan['records']), automatic_jobs=sum(c['execution_mode'] == 'automatic' for c in plan['campaigns']), manual_jobs=sum(c['execution_mode'] == 'manual' for c in plan['campaigns']), provenance=plan['provenance']) if plan else None,
      prepared=[summary(dict(row, jobs=row['jobs']), jobs) for row in rows], uploaded_plan_bytes=MAX_UPLOADED_BYTES,
      limitation='Imported earlier findings retain their original scope. Numerical rechecks are not new network measurements. Manual tasks require domain work. Arbitrary-paper AI extraction is not enabled by this plan bridge.')

def prepare(study, actor, body):
    if set(body) - {'action', 'request_id', 'plan_input'}: raise ValueError('Unsupported preparation fields.')
    request_id = identifier(body.get('request_id'))
    supplied = body.get('plan_input')
    if supplied is not None:
        if not isinstance(supplied, str): raise ValueError('Supply exact original UTF-8 plan text.')
        raw = supplied.encode(); plan = parse(raw, MAX_UPLOADED_BYTES)
    else:
        plan, raw = registered(study)
        if not plan: raise ValueError('No registered preparation plan identifies this paper. Upload a source-grounded plan or prepare its domain-specific source and claim records first.')
    value = compile_plan(plan, raw, study)
    prior = store.rows('prepared_plans', {'study_id': 'eq.' + study['id'], 'id': 'eq.' + value['id'], 'limit': 1})
    if prior: return dict(prepared=summary(dict(prior[0], jobs=prior[0]['jobs']), store.rows('queue_jobs', {'study_id': 'eq.' + study['id']})), replayed=True)
    tick = time.monotonic(); compiled_raw = canonical(value)
    if len(compiled_raw) > 4_000_000: raise ValueError('Compiled preparation exceeds the private artifact budget; split versioned plans without truncating records.')
    originals = [('original-plan', raw, {'transformation': 'none; original source-grounded preparation manifest'}), ('compiled-plan', compiled_raw, {'transformation': VERSION, 'original_manifest_sha256': digest(raw), 'input_transformation': 'Study-qualified identifiers and derived adapter input hashes; scientific values and original review declarations retained.'})]
    artifact_entries = []
    for key, original, provenance in originals:
        if time.monotonic() - tick > 18: raise store.StoreError('Preparation storage budget reached. Originals already uploaded remain private; resume using the same plan.')
        artifact_key = key + ':' + digest(original) if key == 'compiled-plan' else key
        artifact = store.artifact(study['id'], actor, original, str(uuid.uuid5(uuid.UUID(value['id']), artifact_key)), 'application/json', provenance)
        artifact_entries.append(dict(kind='artifacts', record=artifact))
    public_jobs = [{key: job[key] for key in ('id', 'campaign_id', 'title', 'execution_mode')} for job in value['jobs']]
    metadata = dict(plan_id=value['plan_id'], manifest_sha256=value['manifest_sha256'], original_artifact_id=artifact_entries[0]['record']['id'], compiled_artifact_id=artifact_entries[1]['record']['id'], jobs=public_jobs, provenance=value['provenance'])
    row = store.rpc('research_prepare_plan', dict(p_study=study['id'], p_actor=actor, p_id=value['id'], p_request=request_id, p_plan=metadata, p_records=artifact_entries + value['records']))
    return dict(prepared=summary(dict(row, jobs=row['jobs']), store.rows('queue_jobs', {'study_id': 'eq.' + study['id']})), replayed=False)

def enqueue(study, actor, body):
    if set(body) != {'action', 'prepared_id', 'job_id'}: raise ValueError('Choose a retained prepared plan and job; inputs and campaign definitions cannot be overridden.')
    rows = store.rows('prepared_plans', {'study_id': 'eq.' + study['id'], 'id': 'eq.' + identifier(body['prepared_id']), 'limit': 1})
    if not rows: raise ValueError('Prepare this paper before queueing its checks.')
    value = compiled(study['id'], rows[0])
    job = next((j for j in value['jobs'] if j['id'] == identifier(body['job_id'])), None)
    if not job: raise ValueError('Job does not belong to this prepared plan.')
    return scheduler.enqueue(study['id'], actor, job['request'])
