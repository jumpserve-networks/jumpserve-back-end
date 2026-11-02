"""Bounded delivery memory and operational recovery for cyclic evidence reads.

Original numbered requests/responses remain the complete audit. This window is
delivery provenance, never a substantive source review or scientific verdict.
"""
import copy
from pathlib import Path
from workflow import canonical, digest

VERSION = 'autonomy-read-progress-v1'
WINDOW = 32
VIEW_BYTES = 16000
REPEATED_READS = 3
INSTRUCTION = ('Retained evidence pages below were delivered for this exact phase/candidate. '
    'Use them together with evidence_read; next_cursor null ends that document, not a request '
    'to restart at zero. Historical runtime-contract pages apply only to their original '
    'hash-bound reference; use the currently supplied contract identity. Fetch an omitted page only when needed. '
    'After reading the needed evidence, select/propose the method, provide the fresh review '
    'or assess within this phase. Delivery does not establish comprehension, readiness or '
    'scientific coverage. Repeated unchanged reads without progress require operational repair.')


def identity():
    return digest(canonical({name: digest((Path(__file__).parent / name).read_bytes())
        for name in ('autonomy_read_progress.py', 'autonomy.py', 'autonomy_model.py')}))


def scope(context, stage):
    # Each independent review starts its own window, including rejection and a
    # changed candidate in the same role. No earlier judgment is carried across.
    return digest(canonical(dict(stage=stage, proposal=context.get('proposal'),
        selected_check_id=context.get('selected_check_id'))))


def current(context, stage):
    value = context.get('evidence_read_progress')
    if value and value.get('version') == VERSION and value.get('scope_sha256') == scope(context, stage):
        return value
    return None


def _verify(read):
    result = read['result']
    if digest(canonical(result)) != read['result_sha256']:
        raise ValueError('Evidence delivery result hash differs.')
    if 'fragment' in result:
        cursor = read['request']['cursor']; end = cursor + len(result['fragment'])
        if (result['cursor'] != cursor or digest(result['fragment'].encode()) != result['fragment_sha256']
                or end > result['total_characters']
                or result['next_cursor'] != (end if end < result['total_characters'] else None)):
            raise ValueError('Evidence delivery fragment hash differs or cursor is invalid.')
    if 'records' in result and digest(canonical(result['records'])) != result['page_sha256']:
        raise ValueError('Evidence delivery record page hash differs.')


def _key(read):
    result = read['result']
    content = {k: result[k] for k in ('document_sha256', 'fragment_sha256', 'page_sha256') if k in result}
    # Delivery counters in a source-page envelope can change on a reread. The
    # same immutable page is still the same evidence, independent of counters.
    return digest(canonical(dict(request=read['request'], content=content or read['result_sha256'])))


def unchanged_recovery(context):
    incident = context.get('reading_stall') or {}
    value = current(context, incident.get('phase'))
    recovery = context.get('reading_recovery') or {}
    return bool(value and recovery.get('adapter_sha256') == identity()
        and recovery.get('scope_sha256') == value['scope_sha256']
        and recovery.get('new_pages') == value['new_pages'])


def retain(context, stage, read):
    """Record the actual scoped server result, independent of model reason text."""
    _verify(read)
    value = copy.deepcopy(current(context, stage)) or dict(version=VERSION,
        scope_sha256=scope(context, stage), phase=stage, receipts=[], retained_pages=[],
        new_pages=0, repeated_reads=0, omitted_receipts=0)
    key = _key(read)
    previous = next((r for r in value['receipts'] if r['key'] == key), None)
    value['repeated_reads'] = value['repeated_reads'] + 1 if previous else 0
    if not previous:
        value['new_pages'] += 1
    result = read['result']
    receipt = dict(key=key, request=read['request'], result_sha256=read['result_sha256'],
        next_cursor=result.get('next_cursor'), requested_by_turn_id=read.get('requested_by_turn_id'),
        **{k: result[k] for k in ('document_sha256', 'fragment_sha256', 'page_sha256') if k in result})
    value['receipts'] = [r for r in value['receipts'] if r['key'] != key] + [receipt]
    if len(value['receipts']) > WINDOW:
        value['omitted_receipts'] += len(value['receipts']) - WINDOW
        value['receipts'] = value['receipts'][-WINDOW:]
    pages = [p for p in value['retained_pages'] if p['key'] != key]
    pages.append(dict(key=key, read=copy.deepcopy(read)))
    while pages and len(canonical(pages)) > VIEW_BYTES:
        pages.pop(0)
    value['retained_pages'] = pages
    value['omitted_page_views'] = len({r['key'] for r in value['receipts']} - {p['key'] for p in pages})
    context['evidence_read_progress'] = value
    # A new page is material evidence-delivery progress. Merely allocating a
    # new turn, retry or repair record is not progress.
    if value['repeated_reads'] < REPEATED_READS:
        context.pop('reading_stall', None)
        return None
    adapter = identity()
    # The recorded flag describes this incident. Resumption checks the actual
    # installed adapter again, so a qualified later correction can wake it.
    prior = context.get('reading_recovery') or {}
    repeated_recovery = (prior.get('adapter_sha256') == adapter
        and prior.get('scope_sha256') == value['scope_sha256']
        and prior.get('new_pages') == value['new_pages'])
    incident = dict(version=VERSION, phase=stage, scope_sha256=value['scope_sha256'],
        adapter_sha256=adapter, repeated_reads=value['repeated_reads'], new_pages=value['new_pages'],
        repeated_recovery=repeated_recovery, receipts=copy.deepcopy(value['receipts']),
        scope='Repeated unchanged evidence deliveries; no experiment, claim finding or substantive review.')
    context['reading_stall'] = incident
    return incident


def prompt(context, stage):
    value = current(context, stage)
    if value is None:
        return None
    return dict(copy.deepcopy(value), instruction=INSTRUCTION,
        scope='Bounded delivery window only. Omitted pages and all originals remain in numbered controller history; delivery is not review.')


def recover(context, stage):
    """One verified context correction; unchanged recurrence needs platform repair."""
    value = current(context, stage); incident = context.get('reading_stall')
    if value is None or not incident or incident['scope_sha256'] != value['scope_sha256']:
        raise ValueError('Reading stall does not match the original phase/candidate.')
    if unchanged_recovery(context):
        raise ValueError('Reading stalled again without new evidence after the same correction.')
    for page in value['retained_pages']:
        _verify(page['read'])
        if _key(page['read']) != page['key']:
            raise ValueError('Evidence delivery page identity mismatch.')
    corrected = copy.deepcopy(context)
    corrected['reading_recovery'] = dict(version=VERSION, adapter_sha256=identity(),
        scope_sha256=value['scope_sha256'], new_pages=value['new_pages'])
    corrected['last_feedback'] = INSTRUCTION + ' Operational recovery retained the delivered page window. Take a phase-appropriate decision; unchanged recurrence escalates to platform repair.'
    corrected['evidence_read_progress']['repeated_reads'] = 0
    # Keep the original incident in the immutable repair packet, not as an
    # active stop after its exact corrected request passes preflight.
    corrected.pop('reading_stall', None)
    return corrected
