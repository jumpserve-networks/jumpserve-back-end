"""Server-owned admission policies, bound to exact execution/configuration bytes."""
import datetime as dt
import json
import os
from pathlib import Path
import re
import bounded_network
from workflow import canonical, digest

POLICY_PATH = Path(__file__).parent / 'policies/autonomy-experiment-costs-v2.json'

def builtin_quote(raw,today=None):
    spec=json.loads(raw)
    if spec.get('runner')=='source-arithmetic-v1':
        return dict(cents=1,quote=dict(version='source-arithmetic-reservation-v1',kind='conservative-reservation',spec_sha256=digest(raw),experiment_instances=0,measured_charges_usd=None))
    if spec.get('runner')=='built-package-v1':
        policy=json.loads((Path(__file__).parent/'policies/built-package-costs-v1.json').read_bytes())
        if policy.get('approved_for_lab_execution') is not True:raise ValueError('Built package cost envelope awaits operational validation and explicit server-side lab approval; no cloud experiment admitted.')
        today=today or dt.datetime.now(dt.timezone.utc)
        if today>=dt.datetime.fromisoformat(policy['valid_until'].replace('Z','+00:00')): raise ValueError('Built package cost envelope expired.')
        if not re.fullmatch(r'arn:aws:lambda:us-east-1:395567831870:function:[A-Za-z0-9_-]+:[1-9][0-9]*',os.environ.get('NETWORK_BUILT_PACKAGE_FUNCTION','')) or os.environ.get('NETWORK_BUILT_PACKAGE_POLICY_SHA256')!=digest(canonical(policy)): raise ValueError('Built packages need an immutable executor and matching server-owned cost policy.')
        limits=spec['limits']
        if limits['max_instances']!=1 or limits['wall_seconds']>policy['max_wall_seconds'] or limits['max_network_bytes']>policy['max_network_bytes'] or limits['max_output_bytes']>policy['max_output_bytes']: raise ValueError('Built package exceeds its reserved resource envelope.')
        return dict(cents=policy['maximum_cents'],quote=dict(policy,spec_sha256=digest(raw),function_version=os.environ['NETWORK_BUILT_PACKAGE_FUNCTION'],policy_sha256=digest(canonical(policy))))
    if spec.get('runner')!='netem-iperf-v1': return None
    import netem_iperf
    policy=json.loads((Path(__file__).parent/'policies/netem-iperf-costs-v1.json').read_bytes())
    today=today or dt.datetime.now(dt.timezone.utc)
    if today>=dt.datetime.fromisoformat(policy['valid_until'].replace('Z','+00:00')): raise ValueError('TCP sweep cost envelope has expired.')
    netem_iperf.validate(spec['config'])
    if not re.fullmatch(r'arn:aws:lambda:us-east-1:395567831870:function:[A-Za-z0-9_-]+:[1-9][0-9]*',os.environ.get('NETWORK_NETEM_IPERF_FUNCTION','')) or os.environ.get('NETWORK_NETEM_IPERF_POLICY_SHA256')!=digest(canonical(policy)): raise ValueError('TCP sweep needs its immutable executor and matching server-owned cost policy.')
    if not netem_iperf.minimum_wall(spec['config'])<=spec['limits']['wall_seconds']<=policy['max_wall_seconds'] or spec['limits']['max_instances']!=1: raise ValueError('TCP sweep exceeds its approved host or duration envelope.')
    return dict(cents=policy['maximum_cents'],quote=dict(policy,spec_sha256=digest(raw),function_version=os.environ['NETWORK_NETEM_IPERF_FUNCTION'],policy_sha256=digest(canonical(policy))))


def bounded_quote(raw, today=None):
    catalog = json.loads(POLICY_PATH.read_bytes())
    spec = json.loads(raw)
    if spec.get('runner') != bounded_network.VERSION:
        return None
    policy = catalog['bounded_tcp']
    today = today or dt.datetime.now(dt.timezone.utc)
    if today >= dt.datetime.fromisoformat(policy['valid_until'].replace('Z', '+00:00')):
        raise ValueError('Bounded TCP pricing policy has expired; no unpriced experiment was admitted.')
    bounded_network.validate(spec['config'])
    if not re.fullmatch(r'arn:aws:lambda:us-east-1:395567831870:function:[A-Za-z0-9_-]+:[1-9][0-9]*', os.environ.get('NETWORK_BOUNDED_FUNCTION', '')):
        raise ValueError('Bounded TCP needs an immutable deployed Lambda version in the verified account.')
    policy_sha = digest(canonical(policy))
    if os.environ.get('NETWORK_BOUNDED_POLICY_SHA256') != policy_sha:
        raise ValueError('Deployed bounded TCP policy identity differs; preserve the plan.')
    return dict(cents=policy['maximum_cents'], quote=dict(policy, kind='conservative-reservation',
                policy_sha256=policy_sha, spec_sha256=digest(raw),
                function_version=os.environ['NETWORK_BOUNDED_FUNCTION'],
                hard_resource_bounds_verified=True, measured_charges_usd=None))
