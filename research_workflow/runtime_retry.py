"""Retry confirmed transient model failures as NEW durably recorded turns.

Never replay an unconfirmed dispatch, expired lease, experiment, permission
failure or exhausted subscription. Every new turn passes ordinary admission.
"""
import datetime as dt

VERSION='autonomy-runtime-retry-v1'
HTTP={429,500,502,503,504}

def decision(provenance,previous,deadline,now=None):
    if not isinstance(provenance,dict) or provenance.get('status')!='failed' or provenance.get('response_received') is not True or provenance.get('http_status') not in HTTP: return None
    # A 429 with explicit quota exhaustion is not a transient rate-limit retry.
    if provenance.get('error_code') not in {f'http-{x}' for x in HTTP}: return None
    streak=(previous or {}).get('consecutive_failures',0)
    if type(streak) is not int or streak<0: return None
    delay=min(300,15*2**min(streak,5))
    now=now or dt.datetime.now(dt.timezone.utc)
    if dt.datetime.fromisoformat(deadline.replace('Z','+00:00'))<=now+dt.timedelta(seconds=delay+180): return None
    return dict(version=VERSION,consecutive_failures=streak+1,delay_seconds=delay,http_status=provenance['http_status'],
                reason='Confirmed transient HTTP failure. Original failed turn retained; a new numbered turn uses the same claim/method and ordinary budget/deadline admission.')
