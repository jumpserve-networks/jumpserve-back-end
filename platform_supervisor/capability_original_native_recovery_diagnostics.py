"""Incident-only, read-only diagnostics. No recovery/runtime/store imports.

The installed Evidence interface supplies originals, not a spool filesystem API.
A missing private observation is unavailable, never proof that an original is
missing. Optional observations use the contract in the delivered README.
"""
import hashlib
import json
import re
import uuid

SCOPE = {
    'assessment_id': 'fe761052-6e57-4d47-a3de-163c6cad905b',
    'task_id': '2277f9be-ce58-5e9b-b383-b282c7913e45',
    'dispatch_id': '6bf4dac3-01d2-56e1-a273-bea290fe94d1',
    'call_id': 'e1dd7f5c-ab3b-44e1-a8e7-0129968f2f7a',
    'number': 79,
    'request_sha256': '3f91b5124edb8abcb6cdc1f6e1f3bd88c1ddfe2cf4c5cc710fbc3c6f96e0022f',
}
VERSION = 'original-native-recovery-diagnostics-v1'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def bound(value):
    """No free text, transport contents, paths, tokens or stack messages escape."""
    if len(json.dumps(value).encode()) > 12000:
        raise ValueError('Diagnostic exceeds output bound.')
    return value


def indexed(packet):
    result = {}
    for key in ('artifacts', 'native_calls', 'dispatches'):
        for row in packet.get(key, []):
            for a, h in (('id', 'sha256'), ('request_artifact_id', 'request_sha256'),
                         ('response_artifact_id', 'response_sha256')):
                ident, digest = row.get(a), row.get(h)
                if ident and digest:
                    if ident in result and result[ident] != digest:
                        raise ValueError('Ambiguous indexed identity.')
                    result[ident] = digest
    return result


def original(context, index, ref, maximum):
    if not isinstance(ref, dict):
        return None
    ident, digest = ref.get('artifact_id'), ref.get('sha256')
    if (not isinstance(ident, str) or str(uuid.UUID(ident)) != ident or
            not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest) or
            index.get(ident) != digest):
        raise ValueError('Original is outside the indexed scope.')
    raw = context.original(ident)  # Hold/errors propagate to the existing broker.
    if not isinstance(raw, bytes) or len(raw) > maximum or sha(raw) != digest:
        raise ValueError('Original hash or size differs.')
    return raw


def terminal(raw):
    """Conservative SSE terminal check; not builder.validate_calls or recovery."""
    frame = []
    completed = None
    if not raw.endswith(b'\n\n') and not raw.endswith(b'\r\n\r\n'):
        return False
    for line in raw.splitlines():
        if line:
            if line.startswith(b'data:'):
                frame.append(line[5:].lstrip())
            continue
        if not frame:
            continue
        payload = b'\n'.join(frame)
        frame = []
        if payload == b'[DONE]':
            continue
        try:
            event = json.loads(payload)
            kind = event.get('type')
            if kind in ('response.failed', 'response.incomplete', 'error'):
                return False
            if kind == 'response.completed':
                value = event.get('response')
                if completed is not None or not isinstance(value, dict):
                    return False
                if value.get('status') != 'completed' or value.get('model') != 'gpt-6.1-sol':
                    return False
                completed = value
        except (ValueError, AttributeError):
            return False
    return completed is not None and not frame


def collect(context):
    """Only reads trusted packet/indexed originals. Never accepts model scope."""
    p = context.packet
    out = dict(version=VERSION, scope=dict(SCOPE), status='unavailable',
               scientific_completion=False, recovery_executed=False,
               replacement_admitted=False, scan_eligibility='unavailable',
               installed_recovery_source='unavailable', last_scan='unavailable',
               spool='unavailable', completion='unavailable', response='unavailable', request='unavailable',
               prerequisite='existing-private-controller-spool-observation')
    if not isinstance(p, dict) or not p:
        return bound(out)
    if (p.get('assessment_id') != SCOPE['assessment_id'] or
            p.get('assessment_task_id') != SCOPE['task_id'] or
            p.get('task', {}).get('id') != SCOPE['task_id']):
        out['status'] = 'identity-mismatch'
        return bound(out)
    calls = [c for c in p.get('native_calls', []) if c.get('id') == SCOPE['call_id']]
    if len(calls) != 1:
        out['status'] = 'unavailable' if not calls else 'identity-mismatch'
        return bound(out)
    call = calls[0]
    if (any(call.get(k) != SCOPE[k] for k in ('dispatch_id', 'number', 'request_sha256')) or
            any(call.get(k, SCOPE[v]) != SCOPE[v] for k, v in
                (('study_id', 'assessment_id'), ('task_id', 'task_id')))):
        out['status'] = 'identity-mismatch'
        return bound(out)
    index = indexed(p)
    request = original(context, index, dict(artifact_id=call.get('request_artifact_id'),
                                          sha256=call.get('request_sha256')), 256000)
    value = json.loads(request)
    if (not isinstance(value, dict) or value.get('model') != 'gpt-6.1-sol' or
            value.get('store') is not False or value.get('stream') is not True or
            value.get('previous_response_id')):
        out['status'] = 'request-contract-mismatch'
        return bound(out)
    out['request'] = 'hash-verified'
    # No packet producer is assumed to support this optional private observation.
    # Its absence is an explicit prerequisite, not an adapter installation claim.
    observation = p.get('original_native_recovery_observation')
    if observation is None:
        return bound(out)
    if (not isinstance(observation, dict) or observation.get('version') != VERSION or
            observation.get('scope') != SCOPE):
        out['status'] = 'identity-mismatch'
        return bound(out)
    if observation.get('spool_state') in ('available', 'verified-missing'):
        out['spool'] = observation['spool_state']
    source = observation.get('installed_recovery_source')
    if source:
        raw = original(context, index, source, 256000)
        revision = source.get('source_revision')
        if (source.get('path') != 'research_workflow/dispatch_recovery.py' or
                not isinstance(revision, str) or not re.fullmatch('[a-f0-9]{40}', revision)):
            raise ValueError('Installed recovery source binding differs.')
        out['installed_recovery_source'] = dict(sha256=sha(raw), source_revision=revision)
    last = observation.get('last_scan')
    if last:
        raw = original(context, index, last, 64000)
        # Precise exception/refusal stays in the indexed private original. Never
        # emit arbitrary exception strings (which may contain secrets or prompts).
        out['last_scan'] = dict(artifact_id=last['artifact_id'], sha256=sha(raw),
                               bytes=len(raw), detail='private-original-only')
    eligibility = observation.get('scan_eligibility')
    if eligibility in ('eligible', 'ineligible', 'active-transport', 'cleanup-uncertain', 'adapter-exception'):
        out['scan_eligibility'] = eligibility
    for name in ('completion', 'response'):
        if observation.get(name + '_missing') is True:
            if observation.get(name) is not None:
                raise ValueError('Contradictory missing-original observation.')
            out[name] = 'verified-missing'
    completion = original(context, index, observation.get('completion'), 16000)
    response = original(context, index, observation.get('response'), 1048576)
    if completion is not None:
        receipt = json.loads(completion)
        expected = dict(version='builder-native-call-reconciliation-v1',
                        call_id=SCOPE['call_id'], dispatch_id=SCOPE['dispatch_id'],
                        task_id=SCOPE['task_id'], study_id=SCOPE['assessment_id'],
                        request_sha256=SCOPE['request_sha256'], status='complete',
                        response_received=True, http_status=200,
                        response_filename='call-79-response.bin')
        if not isinstance(receipt, dict) or any(type(receipt.get(k)) is not type(v) or receipt[k] != v for k, v in expected.items()):
            out['status'] = 'identity-mismatch'
            return bound(out)
        out['completion'] = 'hash-and-identity-verified'
        if response is not None and (sha(response) != receipt.get('response_sha256') or
                call.get('response_sha256') not in (None, sha(response))):
            out['status'] = 'hash-mismatch'
            return bound(out)
    if response is not None:
        out['response'] = 'terminal-verified' if terminal(response) else 'incomplete-terminal-stream'
    out['status'] = ('active-transport-or-cleanup-uncertainty' if eligibility in ('active-transport', 'cleanup-uncertain')
                     else 'recovery-adapter-exception' if eligibility == 'adapter-exception'
                     else 'observed' if any(out[k] != 'unavailable' for k in
                         ('spool', 'completion', 'response', 'installed_recovery_source', 'last_scan', 'scan_eligibility'))
                     else 'unavailable')
    # Observed capture is deliberately not a reconciliation eligibility proof.
    return bound(out)


CAPABILITY = {'version': 'platform-capability-pack-v1', 'id': 'original-native-recovery-diagnostics', 'revision': 1, 'description': 'Incident-bound read-only original native spool evidence diagnostics; unavailable private observations stay explicit.', 'instructions': 'Read README.md. The verified installed controller collects diagnostics into the indexed diagnostics file. Run python3 summary.py with that JSON on stdin for a bounded summary. No tool input grants scope or runs recovery; private spool observations must come from existing qualified controller evidence. Installation is proved only by the supervisor runtime broker.', 'files': {'summary.py': '"""Render a bounded controller-delivered diagnostic, never collect live data."""\nimport json\nimport sys\nraw = sys.stdin.buffer.read(16001)\nif len(raw) > 16000:\n    raise SystemExit(\'Diagnostic exceeds input bound\')\nvalue = json.loads(raw)\nview = value.get(\'view\', value)\nif not isinstance(view, dict) or view.get(\'version\') != \'original-native-recovery-diagnostics-v1\':\n    raise SystemExit(\'Expected controller-delivered diagnostic\')\n# Fixed summaries only: do not echo untrusted source/exception/transport text.\nallowed = (\'unavailable\', \'identity-mismatch\', \'request-contract-mismatch\', \'hash-mismatch\',\n           \'active-transport-or-cleanup-uncertainty\', \'recovery-adapter-exception\', \'observed\')\nif view.get(\'status\') not in allowed:\n    raise SystemExit(\'Unknown diagnostic state\')\nprint(json.dumps({\'status\': view[\'status\'], \'recovery_executed\': False,\n                  \'scientific_completion\': False,\n                  \'note\': \'Consult the indexed private controller originals; this tool neither verifies installation nor executes recovery.\'}))\n', 'README.md': "# Original native recovery diagnostics (revision 1)\n\nIncident: assessment fe761052-6e57-4d47-a3de-163c6cad905b, task\n2277f9be-ce58-5e9b-b383-b282c7913e45, dispatch\n6bf4dac3-01d2-56e1-a273-bea290fe94d1, call\ne1dd7f5c-ab3b-44e1-a8e7-0129968f2f7a, number 79. Scope is compiled into\nthe installed collector and cannot be expanded by tool input.\n\nUse `python3 summary.py < controller-delivered-diagnostics.json` for a bounded\nsummary. This is not a live probe. Only the verified installed module's collect\nfunction receives context.packet/context.original. Source installation and its\nexact revision are proved by the existing capability inventory/self-update\nbroker, not this tool or any fixture. No credentials, cloud requests, spool\nfilesystem reads, recovery imports or mutation interfaces are used.\n\nPrivate prerequisite: existing qualified controller must supply indexed original\nspool observations. The current packet does NOT contain those observations.\nUnavailable evidence does not mean a missing spool. This pack does not install a\nnew evidence adapter. It explicitly reports this prerequisite rather than trying\nto access the private spool or retrying uncertain calls.\n\nOptional controller-only packet contract: original_native_recovery_observation\nis an object with version original-native-recovery-diagnostics-v1 and scope\nexactly equal to the installed SCOPE. spool_state may be available or verified-missing only after actual private observation. completion and response are optional\n{artifact_id, sha256} references already indexed at packet top level, read only\nwith context.original. completion_missing/response_missing true must represent\nan actual private controller observation of absence, not omitted packet members.\nThey must not coexist with a reference. No unindexed path or model supplied\nreference is accepted. Receipt identities, request contract, response digest and\nbounded complete SSE terminal are checked independently. Completion filenames\nmust match call-79-response.bin. Older workers without sidecars remain explicitly\nmissing/unavailable; a terminal alone does not prove reconciliation eligibility.\n\ninstalled_recovery_source, if available, is an indexed original reference with\npath research_workflow/dispatch_recovery.py and source_revision (40 hex). The\nprivate producer must bind this to installed source, never a checkout candidate.\nThe collector returns its exact verified SHA-256 and revision. Without this\nprivate installed-source evidence the fingerprint remains unavailable.\n\nscan_eligibility is a private controller observation: eligible, ineligible,\nactive-transport, cleanup-uncertain or adapter-exception. It is NOT computed or\nused to admit an operation. last_scan is an indexed original reference (max\n64000 bytes) containing the precise private refusal/exception. Only its exact\nartifact identity/hash/size are projected, not potentially secret exception text.\nThe existing controller retains the private original. Absent diagnostic remains\nunavailable, never successful recovery. Raw response/request/source/stack bytes\nare never returned. Output is under 12000 bytes; all originals bounded to the\nexisting Evidence interface ceiling. Unexpected collector faults propagate to\nthe existing qualified next-revision repair path; persistence Hold is preserved.\n\nNo inference, reconciliation, cleanup, leases, outcome rewrites, replacement,\nstatus/deadline/limit/cancellation edits or scientific claims are performed.\nObservations and successful fixtures are not a recovery qualification. Builder\ncall validation, lock ownership, current transport and server fences still belong\nto the original controller. Release exclusively via supervisor self-update fixed\nchecks and exact installed-source proof, leaving frozen kernel files untouched.\n"}}
