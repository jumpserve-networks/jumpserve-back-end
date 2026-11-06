"""Additive repair-scoped structural diagnostics; never reconciliation or admission."""
from types import SimpleNamespace
from . import capability_retained_provider_outcome_diagnostics as retained
from .common import Hold

CAPABILITY = {
    'version': 'platform-capability-pack-v1',
    'id': 'repair-provider-outcome-diagnostics',
    'revision': 1,
    'description': 'Repair-bound inspection of hash-verified retained provider originals; no reconciliation.',
    'instructions': 'Only the trusted controller invokes collect after frozen qualification, exact installed module hash verification and activation manifest verification. Read its fresh private diagnostics. Unknown outcomes remain fenced. This pack never replays, replaces, settles, admits inference, resumes an assessment or proves scientific completion.',
    'files': {'README.md': 'Repair-scoped host collector only. Requires the exact assessment, task, incident and repair plus exactly one structured incident.failed_turn_id link to the original dispatch. Uses the unchanged installed retained collector for index, bounds, hashes, request, receipt and stream validation. Original reads use context.original only. Reports contain bounded structural facts, never research text or provider response identities. Verified installation and private collection are controller work; a proposal or fixture is not installation or reconciliation.\n'},
}

ASSESSMENT = 'fe761052-6e57-4d47-a3de-163c6cad905b'
TASK = '2d8aa97a-5c01-50ea-a32f-c5025aaf3f9c'
INCIDENT = '8152b5f6-f85b-442a-9dcf-176a261c9042'
REPAIR = '100067cc-c974-5d23-b6f7-9be4da020c12'
DISPATCH = '59d0c555-871e-5971-9461-cdea2f0714cc'


def collect(context):
    report = dict(version='repair-provider-outcome-diagnostics-v1', assessment_id=ASSESSMENT,
                  assessment_task_id=TASK, incident_id=INCIDENT, repair_id=REPAIR,
                  dispatch_id=DISPATCH, status='inconclusive', reason='identity-unavailable',
                  sources={}, reconciled=False, scientific_completion=False,
                  replacement_admitted=False,
                  bounds=dict(bytes_per_original=1048576, events=4096, originals=4))
    try:
        packet = context.packet
        blocker = packet.get('blocker', {})
        if (packet.get('assessment_id') != ASSESSMENT or packet.get('assessment_task_id') != TASK or
            blocker.get('id') != INCIDENT or blocker.get('source_kind') != 'repair' or
            blocker.get('source_id') != REPAIR or not packet.get('platform_task_id')):
            return report
        repairs = packet.get('repairs', [])
        if not isinstance(repairs, list) or any(not isinstance(row, dict) for row in repairs):
            return dict(report, reason='repair-ambiguous-or-missing')
        matches = [row for row in repairs if row.get('id') == REPAIR]
        if len(matches) != 1:
            return dict(report, reason='repair-ambiguous-or-missing')
        incident = matches[0].get('incident')
        if not isinstance(incident, dict) or incident.get('failed_turn_id') != DISPATCH:
            return dict(report, reason='repair-dispatch-link-unavailable')
        links = [row for row in repairs if isinstance(row.get('incident'), dict) and
                 row['incident'].get('failed_turn_id') == DISPATCH]
        if len(links) != 1:
            return dict(report, reason='repair-dispatch-link-ambiguous')
        # All supplied identities must agree. Missing optional row identities are
        # bound by the trusted packet; prose is never used as a link.
        for key, expected in (('study_id', ASSESSMENT), ('task_id', TASK)):
            if key in matches[0] and matches[0][key] != expected:
                return dict(report, reason='repair-identity-mismatch')
        # Adapt only a private shallow packet view, never the original ledger.
        # The predecessor retains every original validation and Hold boundary.
        view = dict(packet, blocker=dict(id=retained.INCIDENT, source_kind='dispatch', source_id=DISPATCH))
        result = retained.collect(SimpleNamespace(packet=view, original=context.original))
        result.update(version=report['version'], incident_id=INCIDENT, repair_id=REPAIR)
        return result
    except Hold:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return dict(report, reason='malformed-evidence')
