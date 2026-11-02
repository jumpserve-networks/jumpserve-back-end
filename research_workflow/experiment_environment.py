"""Recognize retained operational failures without interpreting scientific values.

The controller never installs software on its own host or repeats an invocation.
Only settled experiment originals can request a qualified runtime correction.
"""
import json
from pathlib import Path
import netem_iperf
import store
from workflow import canonical, digest

VERSION = 'experiment-environment-incident-v1'


def failures(provenance):
    found=[]
    for index,command in enumerate(provenance.get('commands',[])):
        if not command.get('returncode') and command.get('error_kind')!='timeout':continue
        argv=command.get('argv',[]);text=command.get('stderr','')+' '+command.get('stdout','')
        code=None
        if 'iperf3' in argv and 'socket buffer size not set correctly' in text:
            code='socket-buffer-unavailable'
        elif 'tc' in argv and 'seed' in argv and ('What is "seed"' in text or 'Illegal "seed"' in text):
            code='seeded-netem-tool-unavailable'
        elif ('tc' in argv and 'del' not in argv and 'help' not in argv) or ('iperf3' in argv and 'server' not in argv):
            code='network-command-failed'
        if code and not any(f['code']==code for f in found):
            found.append(dict(code=code,command_index=index,command_sha256=digest(canonical(command)),
                stderr_excerpt=command.get('stderr','')[:1500],stderr_excerpt_complete=len(command.get('stderr',''))<=1500,
                diagnostic='iperf3 could not obtain the frozen socket buffer.' if code=='socket-buffer-unavailable' else 'tc could not parse the explicit netem seed.' if code=='seeded-netem-tool-unavailable' else 'A networking command failed or timed out; inspect the referenced original command.'))
    preparation=provenance.get('environment_preparation')
    if provenance.get('failure') and ('Experiment environment:' in provenance['failure'] or 'Required Linux tool unavailable:' in provenance['failure'] or preparation and not preparation.get('qualified')):
        found.append(dict(code='environment-preflight-failed',diagnostic=provenance['failure'][:500]))
    return found


def collect(task,jobs,load_runs):
    """Bounded original-byte inspection, also supports pre-repair legacy runs."""
    settled=[j for j in jobs if j.get('run_id') and j['status'] not in ('queued','running','manual')]
    if not settled:return None
    runs=load_runs('runs',task['study_id'],id='in.('+','.join(j['run_id'] for j in settled)+')',limit=1000)
    if {r['id'] for r in runs}!={j['run_id'] for j in settled}:raise ValueError('Environment repair run identity coverage differs.')
    incidents=[];inspected=0
    for run in runs:
        if run['status'] not in ('failed','partial') or run['provenance'].get('runner')!='netem-iperf-v1':continue
        producer=run['provenance']['producer'];known=producer.get('environment_failures')
        if known is None:
            if inspected>=8:continue
            inspected+=1
            raw=store.artifact_bytes(task['study_id'],producer['raw_artifact_id'],maximum=3_000_000)
            if digest(raw)!=run['output_sha256'] or digest(raw)!=producer['raw_envelope_sha256']:
                raise ValueError('Environment repair original envelope hash differs.')
            original=json.loads(raw)
            # Refuse a summary with a different implementation identity.
            if original['provenance'].get('implementation_sha256')!=producer.get('implementation_sha256'):
                raise ValueError('Environment repair implementation identity mismatch.')
            known=failures(original['provenance'])
        if known:
            incidents.append(dict(run_id=run['id'],job_id=next(j['id'] for j in settled if j['run_id']==run['id']),implementation_sha256=producer.get('implementation_sha256'),
                raw_artifact_id=producer['raw_artifact_id'],raw_envelope_sha256=producer['raw_envelope_sha256'],failures=known))
    if not incidents:return None
    return dict(version=VERSION,runner='netem-iperf-v1',incidents=incidents,
        legacy_originals_inspected=inspected,legacy_original_inspection_limit=8,
        scope='Operational failure evidence only; all original runs and valid observations remain unchanged.',
        next_action='Qualify and deploy a corrected fixed environment, then plan a new acquisition with fresh reviews. Never replay these job IDs or relax their frozen configuration.')


def diagnosis(packet):
    if packet.get('version')!=VERSION or packet.get('runner')!='netem-iperf-v1' or not packet.get('incidents'):
        raise ValueError('Environment incident contract differs.')
    current=digest(Path(netem_iperf.__file__).read_bytes())
    failed={i['implementation_sha256'] for i in packet['incidents']}
    if None in failed:raise ValueError('Environment incident lacks the original implementation identity.')
    return dict(version=VERSION,incident=packet,current_implementation_sha256=current,
        waiting=current in failed,resume_stage='planning',
        worker_skill='jumpserve-back-end/research_workflow/skills/network-environment-repair-v1/SKILL.md',
        reason='Experiment environment requires a qualified runtime correction: '+', '.join(sorted({f['code'] for i in packet['incidents'] for f in i['failures']}))+'. Original experiment jobs remain settled; no unchanged replay admitted.',
        qualification='A changed implementation must arrive through the qualified pinned runtime release. This identity check does not prove scientific completion; new reviewed measurements remain required.')


def historical(task,context,load,candidates=None):
    """Adopt settled failures from tasks that passed execution before this release."""
    examined=set(context.get('environment_checked_job_ids',[]))
    identities=list(dict.fromkeys(context.get('job_ids',[])+context.get('completed_job_ids',[])))
    if candidates is not None:identities=[identity for identity in identities if identity in set(candidates)]
    selected=[identity for identity in identities if identity not in examined][:8]
    if not selected:return None
    jobs=load('queue_jobs',task['study_id'],id='in.('+','.join(selected)+')',limit=1000)
    if {j['id'] for j in jobs}!=set(selected):raise ValueError('Historical environment job identity coverage differs.')
    packet=collect(task,jobs,load)
    context['environment_checked_job_ids']=list(dict.fromkeys(context.get('environment_checked_job_ids',[])+
        [j['id'] for j in jobs if j.get('run_id') and j['status'] not in ('queued','running','manual')]))
    return packet


def resume_context(context):
    import copy
    restored=copy.deepcopy(context)
    restored['completed_job_ids']=list(dict.fromkeys(restored.get('completed_job_ids',[])+restored.get('job_ids',[])))
    restored['prior_environment_incidents']=restored.get('prior_environment_incidents',[])+[restored['experiment_environment']]
    restored['environment_checked_job_ids']=list(dict.fromkeys(restored.get('environment_checked_job_ids',[])+
        [incident['job_id'] for incident in restored['experiment_environment'].get('incidents',[]) if incident.get('job_id')]))
    for key in ('experiment_environment','job_ids','campaign_ids','proposal','selected_check_id','reviews','evidence_read'):
        restored.pop(key,None)
    restored['last_feedback']='A qualified environment implementation changed after the retained operational incident. Plan a new acquisition using the current runner contract and the unchanged claim/criterion; retain prior failed and valid observations. Fresh scientific and cost reviews are required. Do not select or replay the settled failed job IDs.'
    return restored
