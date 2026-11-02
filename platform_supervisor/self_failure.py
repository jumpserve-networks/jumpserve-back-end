"""Bounded controller diagnostics for autonomous operational self-repair."""
from pathlib import Path
import traceback
from .common import canonical,digest

VERSION='platform-supervisor-failure-v1'
def original(error):
    return canonical(dict(version=VERSION,error_type=type(error).__name__,traceback=''.join(traceback.format_exception(error))[:100000],
        scope='Private original exception; never delivered as coding instructions or model authority.'))
def capture(error,state):
    # Neither exception text nor locals enter the model view: they can contain
    # private request data or credentials. Original evidence remains retained.
    frames=[dict(file=Path(f.filename).name,function=f.name,line=f.lineno)
        for f in traceback.extract_tb(error.__traceback__)[-12:] if 'platform_supervisor' in Path(f.filename).parts]
    value=dict(version=VERSION,error_type=type(error).__name__,frames=frames)
    fingerprint=digest(canonical(value));previous=state.get('supervisor_failure') or {}
    value.update(fingerprint=fingerprint,repeated=previous.get('repeated',0)+1 if previous.get('fingerprint')==fingerprint else 1)
    state['supervisor_failure']=value
    state['feedback']='The trusted controller recorded an operational failure: '+type(error).__name__+'. Read supervisor-failure.json. Diagnose the recorded stack and implement a focused operational supervisor correction through the dedicated frozen qualification path. Existing authority is sufficient; preserve original actions and do not request new permissions or replay uncertain calls.'
    return value
