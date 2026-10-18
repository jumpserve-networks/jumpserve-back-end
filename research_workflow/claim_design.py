"""Paper-claim design atop the shared, versioned planning/review engine.

The idea_* relation names are retained for compatibility. Paper designs have an
additional immutable origin binding and never become unpublished proposals.
"""
import json
import uuid
from pathlib import Path
import gap_queue
import ideas
import network_spec
import store
from workflow import FIELDS, canonical, digest, identifier, paper_details

VERSION = 'claim-experiment-design-v1'
RESPONSE_VERSION = 'claim-design-response-v3'
POLICY_BYTES = Path(__file__).with_name('policies').joinpath('claim-decision-policy-v1.json').read_bytes()
POLICY = json.loads(POLICY_BYTES)
POLICY_IDENTITY = dict(version=POLICY['version'],sha256=digest(POLICY_BYTES),mode=POLICY['mode'])


def selection(study_id, actor, claim_id):
    snapshot = store.snapshot(study_id, owner=store.owns(study_id, actor))
    if not snapshot or snapshot['study'].get('study_kind', 'paper') != 'paper': raise ValueError('Paper assessment is unavailable.')
    claim = next((c for c in snapshot['records']['claims'] if c['id'] == identifier(claim_id)), None)
    if not claim: raise ValueError('Select a recorded claim from this paper.')
    source = next(s for s in snapshot['records']['sources'] if s['id'] == claim['source_id'])
    gaps = [g for g in snapshot['records']['gaps'] if g['claim_id'] == claim['id']]
    origin = dict(study=snapshot['study'], claim=claim, source=source, gap=max(gaps, key=lambda g: (g['created_at'], g['id'])) if gaps else None)
    return dict(version=VERSION, study_id=study_id, claim_id=claim['id'], claim_sha256=digest(canonical(origin)), claim=claim, source=source), origin


def context(study_id):
    rows = store.rows('claim_designs', {'study_id': 'eq.' + study_id, 'limit': 1})
    if not rows: raise ValueError('This workspace has no retained paper-claim design context.')
    row = rows[0]
    if digest(row['raw_document'].encode()) != row['sha256'] or json.loads(row['raw_document']) != row['document']:
        raise store.StoreError('Claim design context hash or parsed bytes differ.')
    return row


def start(study_id, actor, body):
    gap_selection = set(body) == {'action', 'request_id', 'gap_id', 'gap_sha256'}
    if not (gap_selection or set(body) == {'action', 'request_id', 'claim_id', 'claim_sha256'}) or body['action'] != 'design':
        raise ValueError('Design needs the selected current gap and its exact digest.')
    identifier(body['request_id'])
    if gap_selection:
        preview, snapshot, _, _ = gap_queue.selection(study_id, actor, body['gap_id'])
        if preview['gap_sha256'] != body['gap_sha256']: raise ValueError('The selected claim or source changed; reload before designing.')
        gap = next(g for g in snapshot['records']['gaps'] if g['id'] == body['gap_id'])
        claim = next(c for c in snapshot['records']['claims'] if c['id'] == gap['claim_id'])
        source = next(s for s in snapshot['records']['sources'] if s['id'] == claim['source_id'])
        origin = dict(study=snapshot['study'], claim=claim, source=source, gap=gap)
    else:
        preview, origin = selection(study_id, actor, body['claim_id'])
        if preview['claim_sha256'] != body['claim_sha256']: raise ValueError('The selected claim or source changed; reload before designing.')
    claim, source = origin['claim'], origin['source']
    raw = canonical(origin).decode()
    if len(raw.encode()) > 48000: raise ValueError('Selected claim context exceeds the retained design budget; use an operator export.')
    target = str(uuid.uuid5(uuid.UUID(actor), 'claim-design:' + study_id + ':' + digest(canonical(origin))))
    def uid(name): return str(uuid.uuid5(uuid.UUID(target), name))
    paper = paper_details(dict(title=('Experiment: ' + claim['description'][:160] + ' · ' + origin['study']['title'])[:300],
        paper_url=origin['study']['paper_url'], domain=origin['study']['domain'], study_kind='paper',
        origin_module=origin['study'].get('origin_module'),
        scope='Private experiment design for paper claim ' + claim['id'] + '. Original assessment is unchanged.'))
    store.rpc('research_create_study', dict(p_actor=actor, p_id=target, p_document=paper))
    # Re-key exact records; preserve the original claim/source identities privately.
    copied_source = {key: source.get(key) for key in FIELDS['sources']}
    copied_source['id'] = uid('source')
    copied_claim = {key: claim.get(key) for key in FIELDS['claims']}
    copied_claim.update(id=uid('claim'), source_id=copied_source['id'])
    store.rpc('research_initialize_claim_design', dict(p_study=target, p_actor=actor,
        p_document=origin, p_raw=raw, p_sha256=digest(raw.encode()),
        p_source=copied_source, p_claim=copied_claim))
    draft = dict.fromkeys(ideas.FIELDS, '')
    draft.update(hypothesis=claim['description'], question='Under which documented conditions can this claim be tested?',
        problem='Assess the recorded paper claim at ' + claim['location'] + '.',
        related_work='Original source: ' + source['citation'] + '. Full-text review status: ' + source['review_status'] + '.',
        limitations='The planner receives recorded metadata, not an independently reviewed full text. Existing scientific findings remain unchanged.',
        falsification=POLICY['decision_rule'], protocol=None)
    ideas.save(target, actor, dict(action='save', request_id=uid('initial-packet'), supersedes_id=None,
        document=draft, reason='Initial source-bound paper claim; missing methods and inputs remain empty.'))
    return dict(version=VERSION, study_id=target)


def validate_binding(value, retained):
    ideas.document(value)
    if value['hypothesis'] != retained['document']['claim']['description']:
        raise ValueError('Preserve the exact paper claim as the hypothesis; state narrower tested conditions and coverage separately.')
    protocol = value['protocol']
    source_sha = retained['document']['source'].get('sha256')
    if protocol is not None:
        if protocol.get('decision_policy') != POLICY_IDENTITY or protocol.get('statistics') != POLICY['statistics'] or value['falsification'] != POLICY['decision_rule']:
            raise ValueError('This designer requires its versioned descriptive decision policy. Inferential conclusions need a separately reviewed domain analysis design.')
        if protocol.get('stage') != 'pilot':
            raise ValueError('This designer prepares descriptive pilots; main inference needs a separately reviewed domain analysis design.')
        if protocol.get('hypotheses') != retained['document']['claim']['description']:
            raise ValueError('Candidate protocol must preserve the exact paper hypothesis; limit coverage separately.')
        if protocol.get('execution') and not source_sha:
            raise ValueError('Retrieve and identify the original source bytes before preparing automatic execution.')
        if source_sha and not any(v.get('sha256') == source_sha for v in protocol.get('input_versions', []) if isinstance(v, dict)):
            raise ValueError('Candidate protocol must retain the selected original source hash in input_versions.')
    return value


def save(study_id, actor, body):
    validate_binding(body.get('document'), context(study_id))
    return ideas.save(study_id, actor, body)


def missing(value, retained):
    result = ideas.completeness(value)
    try: validate_binding(value, retained)
    except ValueError as error: result.append(str(error))
    protocol = value.get('protocol') or {}
    if protocol.get('stage') not in ('pilot', 'main'): result.append('Candidate protocol: declare pilot or main stage')
    if protocol.get('experiment_type') not in ('new-measurement', 'independent-check', 'broader-validation'):
        result.append('Candidate protocol: declare the applicable experimental method')
    return result


def accept(study_id, actor, body):
    selected = ideas.packet(study_id, body.get('packet_id'))
    required = missing(selected['document'], context(study_id))
    if required: raise ValueError('Paper experiment plan remains incomplete: ' + '; '.join(required))
    return ideas.accept(study_id, actor, body)


def adopt(study_id, actor, body):
    if set(body) != {'action', 'request_id', 'run_id', 'packet_id'}: raise ValueError('Adopt a retained proposal by its attempt and packet identities only.')
    rows = store.rows('idea_runs', {'study_id': 'eq.' + study_id, 'id': 'eq.' + identifier(body['run_id']), 'limit': 1})
    if not rows or rows[0]['status'] != 'complete' or rows[0]['task'] != 'planning' or rows[0]['packet_id'] != body['packet_id']:
        raise ValueError('A complete proposal for this exact packet is required.')
    proposal = rows[0]['output'].get('proposal')
    if proposal is None:
        raise ValueError('This answer asks clarifying questions; answer them before requesting a complete candidate to adopt. Your saved plan is unchanged.')
    if rows[0]['output'].get('response_version') != RESPONSE_VERSION or rows[0]['output'].get('response_mode') not in ('propose','reuse'):
        raise ValueError('This earlier proposal lacks the corrected planning contract; retain it and request a new proposal.')
    policy = rows[0]['output'].get('decision_policy') or {}
    if any(policy.get(key) != value for key,value in POLICY_IDENTITY.items()) or policy.get('origin') != 'server-policy' or policy.get('model_authored') is not False:
        raise ValueError('This earlier proposal lacks the corrected decision policy; retain it and request a new proposal.')
    validate_binding(proposal, context(study_id))
    selected = ideas.packet(study_id,body['packet_id'])
    previous = selected['document'].get('protocol')
    if previous and proposal['protocol']:
        for key in ('configurations','execution','planned_units','resource_limits','input_versions','metrics'):
            if canonical(proposal['protocol'].get(key)) != canonical(previous.get(key)):
                raise ValueError('AI proposal changed saved '+key+'; explicitly edit and save the plan before fresh reviews.')
    if proposal['protocol'] is not None and rows[0]['output'].get('questions'):
        raise ValueError('Resolve the retained proposal questions before adopting a candidate protocol.')
    if proposal['protocol'] and proposal['protocol'].get('execution'):
        proposal = dict(proposal, protocol=network_spec.bind_inputs(proposal['protocol']))
    if rows[0]['output']['response_mode'] == 'reuse':
        if canonical(proposal) != canonical(selected['document']):
            raise ValueError('A reused plan must match the entire saved packet; save material changes before another review.')
        return store.rpc('research_reuse_claim_packet', dict(p_study=study_id, p_actor=actor,
            p_id=identifier(body['request_id']), p_packet=identifier(body['packet_id']), p_run=identifier(body['run_id'])))
    return ideas.save(study_id, actor, dict(action='save', request_id=body['request_id'], supersedes_id=body['packet_id'],
        document=proposal, reason='Owner adopted retained AI proposal ' + body['run_id'] + '; scientific validity is unestablished.'))


def status(study_id):
    state = ideas.status(study_id); retained = context(study_id)
    packet = next((p for p in state['packets'] if p['id'] == state['current_packet_id']), None)
    if packet: state['missing'] = missing(packet['document'], retained)
    state['ready'] = state['ready'] and not state['missing']
    return dict(state, claim_design=retained, design_version=VERSION)
