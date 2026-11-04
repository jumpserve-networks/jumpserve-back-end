"""Read-only retained transport diagnostics. Never an admission or settlement proof."""
import hashlib
import json
import re
from .common import Hold

CAPABILITY = {'version': 'platform-capability-pack-v1', 'id': 'retained-provider-outcome-diagnostics', 'revision': 1, 'description': 'Task-bound structural inspection of hash-verified retained provider evidence; no reconciliation.', 'instructions': 'The trusted controller invokes collect only from verified installed supervisor source after frozen qualification. Read the delivered diagnostics JSON. Inconclusive results retain the uncertain-call fence. No replay, ledger write, resumption or scientific finding is authorized by this report.', 'files': {'README.md': 'This pack supplies an installed host collector, not a provider client. The controller verifies its installed module hash before invocation. Reports contain only structural facts and source hashes. A terminal observation is not reconciliation, admission, assessment resumption or scientific completion. Missing or ambiguous originals remain inconclusive.\n'}}

ASSESSMENT = 'fe761052-6e57-4d47-a3de-163c6cad905b'
TASK = '2d8aa97a-5c01-50ea-a32f-c5025aaf3f9c'
INCIDENT = 'ef881bcd-9310-413a-be6b-53ef75ba59dc'
DISPATCH = '59d0c555-871e-5971-9461-cdea2f0714cc'
MAX_BYTES = 1048576
MAX_EVENTS = 4096
NAMES = ('logical.json', 'provider-request.json', 'provider-response.bin', 'receipt.json')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('nonfinite JSON')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def provider_request(logical):
    """Pure mirror of the existing data-only controller wire transform."""
    if logical['modelId'] != 'gpt-6.1-sol' or 'builder' in logical:
        raise ValueError('unsupported logical request')
    schema = logical['toolConfig']['tools'][0]['toolSpec']['inputSchema']['json']
    instructions = '\n\n'.join(item['text'] for item in logical['system'])
    instructions += '\nReturn exactly one JSON object satisfying this decision schema. No tools are available.\n' + canonical(schema).decode()
    messages = [dict(role='developer', content='Return exactly one JSON object satisfying the supplied decision schema.')]
    for message in logical['messages']:
        if message['role'] not in ('user', 'assistant') or any(set(c) != {'text'} for c in message['content']):
            raise ValueError('unsupported message')
        messages.append(dict(role=message['role'], content='\n'.join(c['text'] for c in message['content'])))
    return dict(model='gpt-6.1-sol', instructions=instructions, input=messages, store=False, stream=True,
                reasoning=dict(effort='medium'), text=dict(format=dict(type='json_object')), tools=[])


def stream_facts(raw, request):
    facts = dict(bytes=len(raw), frame_complete=False, events=0, terminal_events=0,
                 terminal_kind=None, ordering_consistent=False, identity_consistent=False,
                 observation='inconclusive', reason='malformed-stream')
    if len(raw) > MAX_BYTES:
        return dict(facts, reason='byte-bound-exceeded')
    try:
        text = raw.decode('utf-8').replace('\r\n', '\n')
        if '\r' in text or not text.endswith('\n\n'):
            return dict(facts, reason='truncated-frame')
        blocks = text.split('\n\n')
        if len(blocks) > MAX_EVENTS + 1:
            return dict(facts, reason='event-bound-exceeded')
        response_id = None
        sequence = None
        terminal = None
        done = False
        item_ids = {}
        finished_items = {}
        for block in blocks:
            if not block:
                continue
            data = []; labels = []
            for line in block.split('\n'):
                if line.startswith(':'):
                    continue
                key, sep, value = line.partition(':')
                if not sep or key not in ('data', 'event', 'id', 'retry'):
                    raise ValueError('malformed frame')
                value = value[1:] if value.startswith(' ') else value
                if key == 'data': data.append(value)
                if key == 'event': labels.append(value)
            if not data:
                if labels: raise ValueError('missing event data')
                continue
            if done: raise ValueError('data after done')
            payload = '\n'.join(data)
            if payload == '[DONE]':
                done = True
                if terminal is None: raise ValueError('done without terminal')
                continue
            event = strict_json(payload)
            if not isinstance(event, dict): raise ValueError('event object required')
            facts['events'] += 1
            kind = event.get('type')
            if not isinstance(kind, str) or len(labels) > 1 or labels and labels[0] != kind:
                raise ValueError('event type mismatch')
            if terminal is not None: raise ValueError('event after terminal')
            number = event.get('sequence_number')
            if number is not None:
                if type(number) is not int or number < 0 or sequence is not None and number != sequence + 1:
                    raise ValueError('sequence conflict')
                if sequence is None and number != 0: raise ValueError('missing initial sequence')
                sequence = number
            elif sequence is not None:
                raise ValueError('missing sequence')
            value = event.get('response', {})
            if not isinstance(value, dict): raise ValueError('response object required')
            for identity in (value.get('id'), event.get('response_id')):
                if identity is not None:
                    if not isinstance(identity, str) or not 1 <= len(identity) <= 200:
                        raise ValueError('invalid response identity')
                    if response_id is not None and response_id != identity: raise ValueError('response identity conflict')
                    response_id = identity
            if 'model' in value and value['model'] != request['model']: raise ValueError('model mismatch')
            if kind.startswith('response.output_item.'):
                index = event.get('output_index'); item = event.get('item')
                if type(index) is not int or index < 0 or not isinstance(item, dict) or not isinstance(item.get('id'), str):
                    raise ValueError('item identity missing')
                if index in item_ids and item_ids[index] != item['id']: raise ValueError('item identity conflict')
                item_ids[index] = item['id']
                if kind == 'response.output_item.done':
                    if index in finished_items: raise ValueError('duplicate finished item')
                    finished_items[index] = item
            if kind in ('response.output_text.delta', 'response.output_text.done'):
                index = event.get('output_index')
                if type(index) is not int or index not in item_ids or event.get('item_id') != item_ids[index]:
                    raise ValueError('text item identity conflict')
            if kind in ('response.completed', 'response.failed', 'response.incomplete', 'error'):
                facts['terminal_events'] += 1
                facts['terminal_kind'] = kind
                if kind != 'error':
                    expected = kind.split('.')[1]
                    if value.get('status') != expected or not value.get('id') or value.get('model') != request['model']:
                        raise ValueError('terminal identity/status missing')
                if kind == 'response.completed':
                    output = value.get('output', [])
                    if not isinstance(output, list): raise ValueError('terminal output malformed')
                    for index, identity in item_ids.items():
                        if index >= len(output) or not isinstance(output[index], dict) or output[index].get('id') != identity:
                            raise ValueError('terminal item identity conflict')
                    for index, item in finished_items.items():
                        if output[index] != item: raise ValueError('terminal item content conflict')
                terminal = kind
        facts.update(frame_complete=True, ordering_consistent=True, identity_consistent=response_id is not None)
        if terminal is None: return dict(facts, reason='terminal-unavailable')
        if response_id is None: return dict(facts, reason='response-identity-unavailable')
        # Explicit incomplete/error events are observable, but never successful settlement.
        observation = {'response.completed': 'terminal-completed-observed', 'response.failed': 'terminal-failed-observed'}.get(terminal, 'inconclusive')
        return dict(facts, observation=observation, reason='terminal-observed' if observation != 'inconclusive' else 'incomplete-or-error-terminal')
    except (ValueError, TypeError, KeyError, RecursionError):
        return facts


def collect(context):
    report = dict(version='retained-provider-outcome-diagnostics-v1', assessment_id=ASSESSMENT,
                  assessment_task_id=TASK, incident_id=INCIDENT, dispatch_id=DISPATCH,
                  status='inconclusive', reason='identity-unavailable', sources={},
                  reconciled=False, scientific_completion=False, replacement_admitted=False,
                  bounds=dict(bytes_per_original=MAX_BYTES, events=MAX_EVENTS, originals=4))
    try:
        packet = context.packet
        if (packet.get('assessment_id') != ASSESSMENT or packet.get('assessment_task_id') != TASK or
            packet.get('blocker', {}).get('id') != INCIDENT or packet['blocker'].get('source_id') != DISPATCH or
            packet['blocker'].get('source_kind') != 'dispatch' or not packet.get('platform_task_id')):
            return report
        dispatches = [d for d in packet.get('dispatches', []) if d.get('id') == DISPATCH]
        if len(dispatches) != 1: return dict(report, reason='dispatch-ambiguous-or-missing')
        dispatch = dispatches[0]
        for key, expected in (('study_id', ASSESSMENT), ('task_id', TASK)):
            if key in dispatch and dispatch[key] != expected: return dict(report, reason='dispatch-identity-mismatch')
        refs = {}
        for ref in dispatch.get('provenance', {}).get('artifacts', []):
            name = ref.get('name')
            if name not in NAMES: continue
            if name in refs: return dict(report, reason='artifact-ambiguous')
            refs[name] = ref
        if set(refs) != set(NAMES): return dict(report, reason='original-unavailable')
        indexed = {}
        for ref in packet.get('artifacts', []):
            ident = ref.get('id'); digest = ref.get('sha256')
            if ident in indexed and indexed[ident] != digest: return dict(report, reason='index-conflict')
            indexed[ident] = digest
        if len({r.get('id') for r in refs.values()}) != 4: return dict(report, reason='artifact-alias-conflict')
        retained = {}
        for name in NAMES:
            ref = refs[name]; ident = ref.get('id'); digest = ref.get('sha256')
            if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest) or indexed.get(ident) != digest:
                return dict(report, reason='original-not-indexed')
            if type(ref.get('byte_count')) is not int or not 0 <= ref['byte_count'] <= MAX_BYTES:
                return dict(report, reason='original-byte-bound')
            try:
                raw = context.original(ident)
            except Hold:
                raise
            except (ValueError, OSError, KeyError):
                return dict(report, reason='original-unavailable')
            if not isinstance(raw, bytes) or len(raw) != ref['byte_count'] or sha(raw) != digest:
                return dict(report, reason='original-hash-or-size-mismatch')
            retained[name] = raw
            report['sources'][name] = dict(artifact_id=ident, sha256=digest, bytes=len(raw))
        request_hash = dispatch.get('request_sha256')
        if request_hash != sha(retained['logical.json']): return dict(report, reason='logical-request-mismatch')
        report['request_sha256'] = request_hash
        logical = strict_json(retained['logical.json']); request = strict_json(retained['provider-request.json'])
        receipt = strict_json(retained['receipt.json'])
        if provider_request(logical) != request: return dict(report, reason='provider-request-mismatch')
        provenance = dispatch['provenance']
        # request_hash in this adapter binds the native bridge request, not the logical/provider JSON.
        for key in ('request_hash', 'http_status', 'provider_calls', 'provider_request_sent', 'response_received', 'provider_retries', 'paid_fallback', 'status', 'error_code', 'model_id'):
            if key in provenance and receipt.get(key) != provenance[key]: return dict(report, reason='receipt-provenance-mismatch')
        if receipt.get('context_transport', {}).get('original_logical_sha256') != request_hash:
            return dict(report, reason='receipt-logical-mismatch')
        if receipt.get('model_id') != request['model'] or receipt.get('provider_calls') != 1 or receipt.get('response_received') is not True:
            return dict(report, reason='receipt-transport-unconfirmed')
        report['stream'] = stream_facts(retained['provider-response.bin'], request)
        report.update(status='structural-diagnostics', reason='retained-bytes-inspected')
        return report
    except Hold:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return dict(report, reason='malformed-evidence')
