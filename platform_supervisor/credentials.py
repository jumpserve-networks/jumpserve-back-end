"""EC2 role credentials refresh in botocore; secrets rotate outside the sandbox."""
import json
import os
import threading
import time
from .common import Hold

class Credentials:
    def __init__(self, session=None):
        if any(os.environ.get(key) for key in ('AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY','AWS_SESSION_TOKEN','AWS_PROFILE')):raise Hold('Platform production service must use its EC2 instance role, not static keys or a human profile.')
        if session is None:
            import boto3
            session=boto3.Session(region_name='us-east-1')
        if session.get_credentials().method!='iam-role':raise Hold('EC2 instance role credentials are required for unattended platform operation.')
        self.client=session.client('secretsmanager');self.cache={};self.lock=threading.Lock()

    def secret(self, arn):
        if not arn.startswith('arn:aws:secretsmanager:us-east-1:395567831870:secret:jumpserve/'):raise Hold('Service secret target differs.')
        with self.lock:
            cached=self.cache.get(arn)
            if cached and time.monotonic()-cached[0]<300:return cached[1]
            raw=self.client.get_secret_value(SecretId=arn)['SecretString']
            self.cache[arn]=(time.monotonic(),raw)
            return raw

    def supabase(self):return self.secret(os.environ['SUPABASE_SECRET_ARN'])
    def github(self):
        value=json.loads(self.secret(os.environ['PLATFORM_SECRET_ARN']))
        token=value.get('github_token')
        if not isinstance(token,str) or not token:raise Hold('Private GitHub publisher credential is missing.')
        return token
    def context7(self):return json.loads(self.secret(os.environ['PLATFORM_SECRET_ARN'])).get('context7_api_key')
