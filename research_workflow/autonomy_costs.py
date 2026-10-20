"""Server-owned admission policies, bound to exact execution/configuration bytes."""
import datetime as dt
import json
import os
from pathlib import Path
import re
import bounded_network
from workflow import canonical, digest

POLICY_PATH = Path(__file__).parent / 'policies/autonomy-experiment-costs-v2.json'


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
