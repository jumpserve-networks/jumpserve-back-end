"""Durable capability authoring and delivery of verified installed tool packs.

A candidate requests implementation, never grants itself authority or declares
itself installed. The existing self-update broker owns qualification and AWS
activation. This module never executes candidate modules or tool scripts.
"""
import ast
import json
from pathlib import Path, PurePosixPath
import re
import types
import uuid

VERSION='platform-capability-request-v1'
PACK='platform-capability-pack-v1'
BACKEND='jumpserve-networks/jumpserve-back-end'
REQUEST='capability-request.json'


class CapabilityFault(ValueError):
    """A verified collector failed; its next revision needs implementation."""


class Evidence:
    """Task-bound reads for installed diagnostic collectors; no credential API."""
    def __init__(self,store,packet):
        self._store=store;self.packet=packet or {};self._allowed={}
        for key in ('native_calls','dispatches','artifacts'):
            for row in self.packet.get(key,[]):
                for id_key,sha_key in (('request_artifact_id','request_sha256'),('response_artifact_id','response_sha256'),('id','sha256')):
                    ident,sha=row.get(id_key),row.get(sha_key)
                    if ident and sha:
                        previous=self._allowed.setdefault(ident,sha)
                        if previous!=sha:raise ValueError('Ambiguous original evidence identity.')
    def _read(self,path,**kwargs):
        from .common import Hold
        try:return self._store.request(path,**kwargs)
        except Hold:raise
        except (OSError,ValueError):
            raise Hold('Original diagnostic persistence read is unconfirmed; retain its identity and retry without inference.','reconciling') from None
    def original(self,artifact_id):
        from .common import digest,Hold
        ident=str(uuid.UUID(artifact_id));expected=self._allowed.get(ident)
        if not expected or not re.fullmatch('[a-f0-9]{64}',expected):raise ValueError('Original is outside this task evidence packet.')
        study=str(uuid.UUID(self.packet['assessment_id']))
        rows=self._read('/rest/v1/research_artifacts?id=eq.'+ident+'&study_id=eq.'+study+
            '&select=id,sha256,byte_count,storage_path&limit=2')
        if len(rows)!=1 or rows[0]['sha256']!=expected or rows[0]['storage_path']!=study+'/'+expected:
            raise Hold('Original evidence pointer differs; retain its recorded identity.','reconciling')
        if type(rows[0]['byte_count']) is not int or not 0<=rows[0]['byte_count']<=1048576:
            raise ValueError('Original exceeds the diagnostic read bound.')
        raw=self._read('/storage/v1/object/research-workflow-raw/'+study+'/'+expected,raw=True)
        if len(raw)!=rows[0]['byte_count'] or digest(raw)!=expected:raise Hold('Original evidence bytes differ; no replacement was used.','reconciling')
        return raw


def validate_request(value):
    if (not isinstance(value,dict) or set(value)!={'version','id','minimum_revision','objective','acceptance'}
        or value['version']!=VERSION or not isinstance(value['id'],str)
        or not re.fullmatch(r'[a-z][a-z0-9-]{1,62}',value['id'])
        or type(value['minimum_revision']) is not int or not 1<=value['minimum_revision']<=1000000
        or not isinstance(value['objective'],str) or not 1<=len(value['objective'])<=8000
        or not isinstance(value['acceptance'],list) or not 1<=len(value['acceptance'])<=12
        or any(not isinstance(x,str) or not 1<=len(x)<=500 for x in value['acceptance'])):
        raise ValueError('Invalid capability implementation request.')
    return value


def module_name(key):
    if not isinstance(key,str) or not re.fullmatch(r'[a-z][a-z0-9-]{1,62}',key):
        raise ValueError('Invalid capability identifier.')
    return 'capability_'+key.replace('-','_')+'.py'


def descriptor(raw,name):
    """Inspect the literal contract without importing proposed executable code."""
    tree=ast.parse(raw);declarations=[]
    for node in tree.body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='CAPABILITY' for t in node.targets):
            if len(node.targets)!=1:raise ValueError('Capability contract must have one assignment.')
            declarations.append(node.value)
    if len(declarations)!=1:raise ValueError('One literal CAPABILITY contract is required.')
    value=ast.literal_eval(declarations[0])
    if (not isinstance(value,dict) or set(value)!={'version','id','revision','description','instructions','files'}
        or value['version']!=PACK or module_name(value.get('id'))!=name
        or type(value['revision']) is not int or not 1<=value['revision']<=1000000
        or any(not isinstance(value[k],str) or not 1<=len(value[k])<=8000 for k in ('description','instructions'))
        or not isinstance(value['files'],dict) or not 1<=len(value['files'])<=16):
        raise ValueError('Invalid capability pack contract.')
    total=0
    for filename,content in value['files'].items():
        if not isinstance(filename,str):raise ValueError('Capability filenames must be strings.')
        path=PurePosixPath(filename)
        if (not isinstance(filename,str) or not 1<=len(filename)<=160 or path.is_absolute()
            or path.as_posix()!=filename or '\\' in filename or any(p in ('.','..') or p.startswith('.') for p in path.parts)
            or any(not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*',p) for p in path.parts)
            or path.suffix not in ('.py','.sh','.json','.md','.txt')
            or not isinstance(content,str) or len(content.encode())>256000):
            raise ValueError('Invalid capability tool file.')
        total+=len(content.encode())
        if any(marker in content for marker in ('-----BEGIN PRIVATE KEY','github_pat_','sb_secret_')):
            raise ValueError('Capability source contains a credential marker.')
    if total>1000000:raise ValueError('Capability pack exceeds its source bound.')
    return value


def rpc(store,operation,**payload):
    return store.request('/rest/v1/rpc/platform_capability_worker','POST',dict(
        p_operation=operation,p_worker=store.worker,p_task=store.task['id'],
        p_token=store.task['lease_token'],p_payload=payload))


def inventory(store,root=None):
    from .common import digest,Hold
    root=Path(root) if root is not None else Path(__file__).resolve().parent
    manifest=store.request('/rest/v1/rpc/platform_supervisor_runtime','POST',{})
    if not isinstance(manifest,dict) or not isinstance(manifest.get('files'),dict):
        raise Hold('Verified installed supervisor manifest is unavailable.','reconciling')
    result=[]
    for name,sha in sorted(manifest['files'].items()):
        if not re.fullmatch(r'capability_[a-z][a-z0-9_]*\.py',name):continue
        path=root/name
        if path.is_symlink() or not path.is_file() or path.stat().st_size>1500000:
            raise Hold('Installed capability source is missing or unsafe.','reconciling')
        raw=path.read_bytes()
        if digest(raw)!=sha:raise Hold('Installed capability source hash differs.','reconciling')
        pack=descriptor(raw,name)
        result.append(dict(pack,module=name,module_sha256=sha,source_revision=manifest['source_revision']))
    return result


def deliver(controller,workspace):
    from .common import canonical,digest,save,Hold
    installed=inventory(controller.store)
    context=rpc(controller.store,'context')
    resume_progress(controller,workspace,installed)
    packet=None
    index=[]
    for pack in installed:
        folder=Path(workspace)/'registered-capabilities'/pack['id']/pack['module_sha256']
        files=[]
        for name,content in sorted(pack['files'].items()):
            path=folder/name;raw=content.encode()
            if any(p.is_symlink() for p in [path,*path.parents] if p.is_relative_to(Path(workspace))):
                raise Hold('Capability delivery path contains a link.','reconciling')
            if path.exists() and path.read_bytes()!=raw:
                raise Hold('A delivered capability tool was modified; retain its original identity.','reconciling')
            if not path.exists():save(path,raw)
            files.append(dict(path=path.relative_to(workspace).as_posix(),sha256=digest(raw)))
        index.append({k:v for k,v in pack.items() if k!='files'}|dict(files=files))
        path=Path(__file__).resolve().parent/pack['module']
        # Only an actually installed hash-verified module can contribute a host
        # collector. Candidate code and delivered tool scripts are never imported.
        if context is None and path.exists() and any(isinstance(n,ast.FunctionDef) and n.name=='collect' for n in ast.parse(path.read_bytes()).body):
            if packet is None and controller.store.task.get('assessment_repair'):
                packet=controller.store.request('/rest/v1/rpc/platform_repair_context','POST',dict(
                    p_worker=controller.store.worker,p_task=controller.store.task['id'],p_token=controller.store.task['lease_token']))
                if not isinstance(packet,dict) or packet.get('platform_task_id')!=controller.store.task['id']:
                    raise Hold('Capability collector evidence is not task-bound.','reconciling')
            collect(controller,workspace,pack,Evidence(controller.store,packet),index[-1])
    value=dict(version=PACK,installed=index,implementation=context,
        instructions='These tool packs are bound to verified installed supervisor source. Read each pack instruction and invoke its files with the existing sandbox exec tool. No credential or host shell is exposed. To obtain a missing tool or adapter, write capability-request.json using the supplied contract. The controller creates implementation work under existing standing authority; do not ask the owner for another permission.')
    save(Path(workspace)/'references/capabilities/index.json',canonical(value))
    controller.state['capability_implementation']=context
    return value


def collect(controller,workspace,pack,evidence,entry):
    """Run verified read-only diagnostics; a fault creates a repair request."""
    from .common import canonical,digest,save,Hold
    from . import self_failure
    path=Path(__file__).resolve().parent/pack['module']
    raw=path.read_bytes()
    if path.is_symlink() or digest(raw)!=pack['module_sha256']:
        raise Hold('Installed collector changed before invocation.','reconciling')
    try:
        module=types.ModuleType('platform_supervisor.'+path.stem)
        module.__package__='platform_supervisor';module.__file__=str(path)
        exec(compile(raw,str(path),'exec'),module.__dict__)
        value=module.collect(evidence)
        original=canonical(value)
        if not isinstance(value,dict) or len(original)>1000000:raise ValueError('Bounded diagnostic object required.')
    except Hold:
        raise  # Transient persistence/identity uncertainty is not a code upgrade.
    except Exception as error:
        failure=controller.store.artifact('capability-collector-failure-'+pack['id']+'.json',self_failure.original(error))
        diagnostic=self_failure.capture(error,{})
        request_spec=dict(version=VERSION,id=pack['id'],minimum_revision=pack['revision']+1,
            objective='Repair the installed diagnostic collector for '+pack['id']+'; its verified source '+pack['module_sha256']+
                ' raised '+type(error).__name__+'. Sanitized stack: '+json.dumps(diagnostic['frames'])+
                '. Original private exception artifact: '+failure['id']+'. Preserve the existing tool contract and original evidence.',
            acceptance=['Add a regression for the recorded collector fault.','Return a bounded JSON diagnostic view while retaining task binding, exact hashes and privacy.'])
        request_path=Path(workspace)/REQUEST
        if request_path.exists() and not request_path.is_symlink():controller.store.artifact(REQUEST,request_path.read_bytes())
        save(request_path,canonical(validate_request(request_spec)))
        raise CapabilityFault('Installed capability collector requires its next qualified revision.') from None
    artifact=controller.store.artifact('capability-diagnostics-'+pack['id']+'.json',original)
    from scheduler import shared_document
    view,projection=shared_document(value)
    result=canonical(dict(original_artifact_id=artifact['id'],original_sha256=digest(original),view=view,projection=projection))
    target=Path(workspace)/'references/capabilities'/('diagnostics-'+pack['id']+'.json')
    save(target,result);entry['diagnostics']=dict(path=target.relative_to(workspace).as_posix(),sha256=digest(result))


def resume_progress(controller,workspace,installed):
    """A proven new tool can retire its old local stop; originals stay private."""
    from .common import canonical,digest,save,Hold
    ident=controller.state.get('capability_activation')
    if not ident or controller.state.get('capability_progress_restored')==ident:return
    ident=str(uuid.UUID(ident));task=str(uuid.UUID(controller.store.task['id']))
    rows=controller.store.request('/rest/v1/platform_capability_requests?id=eq.'+ident+
        '&parent_task_id=eq.'+task+'&select=id,capability_id,minimum_revision&limit=2')
    proofs=controller.store.request('/rest/v1/platform_capability_activations?request_id=eq.'+ident+
        '&select=module_sha256,revision&limit=2')
    if len(rows)!=1 or len(proofs)!=1:return
    available=any(p['id']==rows[0]['capability_id'] and p['revision']>=rows[0]['minimum_revision']
                  and p['module_sha256']==proofs[0]['module_sha256'] for p in installed)
    if not available:return
    retained=[]
    for name in ('progress.json',REQUEST):
        path=Path(workspace)/name
        if not path.exists() and not path.is_symlink():continue
        if path.is_symlink() or not path.is_file() or path.stat().st_size>32000:
            raise Hold('Original capability stop is unsafe; retain it for reconciliation.','reconciling')
        raw=path.read_bytes()
        if name==REQUEST:
            try:request=validate_request(json.loads(raw))
            except (ValueError,UnicodeError):continue
            if request['id']!=rows[0]['capability_id'] or request['minimum_revision']>proofs[0]['revision']:continue
        artifact=controller.store.artifact('capability-predecessor-'+name+'-'+digest(raw)+'.json',raw)
        save(Path(workspace)/'references/capabilities'/('previous-'+digest(raw)+'.json'),raw)
        retained.append(dict(name=name,artifact_id=artifact['id'],sha256=digest(raw)))
        path.unlink()
    controller.state['capability_progress_restored']=ident
    controller.state['capability_resume_evidence']=dict(activation_id=ident,originals=retained)
    controller.state['feedback']='A required capability now has verified installed source. Read references/capabilities/index.json and continue the original objective. Earlier local stops were retained as private artifacts; no original provider outcome or scientific criterion changed.'


def request(controller,workspace):
    from .common import canonical,digest,identity,save,Hold
    path=Path(workspace)/REQUEST
    if not path.exists() and not path.is_symlink():return None
    if path.is_symlink() or not path.is_file() or path.stat().st_size>16000:
        raise Hold('Capability request must be a bounded plain JSON file.')
    raw=path.read_bytes()
    try:spec=validate_request(json.loads(raw))
    except (ValueError,TypeError,UnicodeError):
        raise Hold('Correct capability-request.json using the declared request contract; original working bytes are retained.','implementing') from None
    # Preserve exactly what the coding turn requested, before any queue effect.
    original=controller.store.artifact(REQUEST,raw)
    ident=identity(controller.store.task['id'],'capability:'+digest(canonical(spec)))
    result=rpc(controller.store,'request',id=ident,spec=spec,artifact_id=original['id'])
    controller.state['capability_wait']=result
    if result.get('activation_lost') is True:
        spec['minimum_revision']=result['minimum_revision'];validate_request(spec)
        save(path,canonical(spec))
        raise Hold('A verified capability was removed by a later runtime; original request retained and the next tool revision will be rebuilt automatically.','implementing')
    if result.get('available') is True:
        path.unlink()  # Original bytes remain in the immutable artifact above.
        controller.state['feedback']='The requested capability is installed. Read references/capabilities/index.json and continue the original objective with the delivered tool. Do not request it again or infer that any experiment succeeded.'
        controller.state.pop('repair_guard',None)
        raise Hold('Verified capability is available; original work can continue.','implementing')
    raise Hold('Required capability implementation queued automatically as '+result['implementation_task_id']+'; original work waits for verified activation.','blocked')


def validate_candidate(controller,directory):
    context=controller.state.get('capability_implementation')
    if not context:return
    path=Path(directory)/'platform_supervisor'/module_name(context['spec']['id'])
    if path.is_symlink() or not path.is_file():
        from .common import Hold
        raise Hold('Capability implementation must include its declared operational tool pack.','implementing')
    try:pack=descriptor(path.read_bytes(),path.name)
    except (ValueError,TypeError,SyntaxError,UnicodeError):
        from .common import Hold
        raise Hold('Correct the declared capability pack contract before qualification.','implementing') from None
    if pack['revision']<context['spec']['minimum_revision']:
        from .common import Hold
        raise Hold('Capability revision does not satisfy the recorded request.','implementing')
    baseline=controller.state.get('sources',{}).get(BACKEND,{}).get('snapshot',{})
    new_tests=[]
    for test in (Path(directory)/'platform_supervisor/tests').glob('test_*.py'):
        relative=test.relative_to(directory).as_posix()
        if relative in baseline or test.is_symlink():continue
        try:tree=ast.parse(test.read_bytes())
        except (SyntaxError,UnicodeError):continue
        cases=[c for c in tree.body if isinstance(c,ast.ClassDef) and any(
            isinstance(b,ast.Attribute) and b.attr=='TestCase' or isinstance(b,ast.Name) and b.id=='TestCase' for b in c.bases)]
        if any(isinstance(n,ast.FunctionDef) and n.name.startswith('test_') and not n.decorator_list
            and any(isinstance(x,ast.Assert) or isinstance(x,ast.Call) and isinstance(x.func,ast.Attribute) and x.func.attr.startswith('assert') for x in ast.walk(n))
            for c in cases for n in c.body):new_tests.append(relative)
    if not new_tests:
        from .common import Hold
        raise Hold('Capability implementation needs a new discoverable regression with assertions; existing checks remain unchanged.','implementing')


def activate(controller):
    context=controller.state.get('capability_implementation')
    if not context:return None
    from .common import Hold
    matches=[p for p in inventory(controller.store) if p['id']==context['spec']['id'] and p['revision']>=context['spec']['minimum_revision']]
    if len(matches)!=1:raise Hold('Required capability is not verified in the installed runtime.','verifying')
    pack=matches[0]
    return rpc(controller.store,'activate',request_id=context['id'],module_sha256=pack['module_sha256'],
        revision=pack['revision'],source_revision=pack['source_revision'])


def instruction():
    return ('Read references/capabilities/index.json for verified installed tools and any capability implementation objective. '
        'If a missing operational tool or diagnostic adapter blocks progress, write capability-request.json with version '+VERSION+
        ', id (lowercase kebab case), minimum_revision (positive integer), objective, and acceptance (a list of concrete checks). '
        'This requests implementation; it never certifies code or grants authority. The supervisor builds and qualifies it without another owner question. '
        'Implementation tasks add platform_supervisor/capability_<id with underscores>.py with one literal CAPABILITY dict: version '+PACK+
        ', id, revision, description, instructions, files (relative .py/.sh/.json/.md/.txt names mapped to exact tool source strings). '
        'Add meaningful new regression tests, preserve existing checks, and use the operational self-update route. Do not mix unrelated application edits into a self-update. '
        'An optional collect(context) function can supply task-bound diagnostics after verified installation. context.packet is the trusted evidence packet and context.original(artifact_id) reads only its indexed, hash-verified originals. Return a bounded JSON object; the controller retains it privately and redacts the model view. A collector fault automatically requests its next revision. '
        'Tools execute in the existing isolated coding workspace. Missing credentials or new cloud/runtime/database adapters require their qualified service implementation, never a fabricated success, another owner grant or a replay of an uncertain operation. ')
