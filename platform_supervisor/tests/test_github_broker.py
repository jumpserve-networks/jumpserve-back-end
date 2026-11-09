"""Frozen publication identities and merge checks with a synthetic GitHub API."""
import base64
import copy
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch
import urllib.error
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from platform_supervisor import github
from platform_supervisor.common import Hold, POLICY, canonical


class Transport(unittest.TestCase):
    def test_repository_scope_rotating_header_credential_and_error_status_do_not_expose_secret_bytes(self):
        opener = MagicMock(); opener.open.side_effect = [io.BytesIO(b'{"recorded":true}'), io.BytesIO(b'')]
        tokens = MagicMock(side_effect=['synthetic-first-token', 'synthetic-second-token'])
        with patch.object(github, 'network_opener', return_value=opener): broker = github.GitHub(tokens)
        repository = next(iter(POLICY['repositories']))
        self.assertEqual(broker.request('POST', '/repos/' + repository + '/git/blobs', {'zero': 0}), {'recorded': True}); request = opener.open.call_args.args[0]; self.assertEqual(request.data, b'{"zero":0}'); self.assertEqual(request.get_header('Authorization'), 'Bearer synthetic-first-token'); self.assertNotIn('synthetic-first-token', request.full_url); self.assertEqual(opener.open.call_args.kwargs, {'timeout': 30})
        self.assertIsNone(broker.get(repository, 'git/ref/heads/main')); self.assertIsNone(opener.open.call_args.args[0].data); self.assertEqual(opener.open.call_args.args[0].get_header('Authorization'), 'Bearer synthetic-second-token')
        for path in ['/user', '/repos/unrelated/project', '/repos/' + repository + '/../../other', '/repos/' + repository + '-suffix']:
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'repository scope'): broker.request('GET', path)
        self.assertEqual(tokens.call_count, 2)
        for token in [None, '', 'Bearer synthetic']:
            with self.subTest(token=token), self.assertRaisesRegex(Hold, 'scoped GitHub publisher'): github.GitHub(token)
        with patch.object(github, 'network_opener', return_value=opener): broker = github.GitHub('synthetic-token')
        opener.open.side_effect = urllib.error.HTTPError('https://private.example.test/secret', 503, 'private response', {}, None)
        with self.assertRaises(github.ApiError) as error: broker.get(repository, 'git/ref/heads/main')
        self.assertEqual(error.exception.code, 503); self.assertEqual(error.exception.status, 'reconciling'); self.assertNotIn('private', str(error.exception)); self.assertIsNone(error.exception.__cause__)
        self.assertIsNone(github.NoRedirect().redirect_request(None, None, None, None, None, None))

    def test_responses_and_public_archives_are_bounded_and_archive_reads_never_receive_credentials(self):
        opener = MagicMock()
        with patch.object(github, 'network_opener', return_value=opener): broker = github.GitHub('synthetic-token')
        repository = next(iter(POLICY['repositories'])); revision = 'a' * 40
        opener.open.return_value = io.BytesIO(b'x' * 5000001)
        with self.assertRaisesRegex(Hold, 'response exceeds'): broker.get(repository, 'synthetic')
        opener.open.return_value = io.BytesIO(b'Original archive bytes'); self.assertEqual(broker.archive(repository, revision), b'Original archive bytes'); request = opener.open.call_args.args[0]; self.assertEqual(request.full_url, 'https://codeload.github.com/' + repository + '/tar.gz/' + revision); self.assertIsNone(request.get_header('Authorization')); self.assertEqual(opener.open.call_args.kwargs, {'timeout': 60})
        for repo, sha in [('unrelated/project', revision), (repository, 'main')]:
            with self.subTest(repo=repo, sha=sha), self.assertRaisesRegex(ValueError, 'Exact authorized revision'): broker.archive(repo, sha)
        opener.open.return_value = io.BytesIO(b'x' * 100000001)
        with self.assertRaisesRegex(Hold, 'archive exceeds'): broker.archive(repository, revision)


class Publication(unittest.TestCase):
    def setUp(self):
        with patch.object(github, 'network_opener'): self.broker = github.GitHub('synthetic-token')
        self.repository = next(iter(POLICY['repositories'])); self.base, self.head, self.tree = 'a' * 40, 'b' * 40, 'c' * 40
        self.task = dict(id=str(uuid.uuid4()), created_at='2026-11-01T00:00:00Z', title='Private research objective must not be public', objective='Private confidential assessment details')
        self.change = dict(id=str(uuid.uuid4()), repository=self.repository, base_sha=self.base, head_sha=self.head, pr_number=17, patch=dict(tree_sha256='d' * 64, files=[dict(path='source.py', mode='100644', content=base64.b64encode(b'# Original tested implementation\n').decode()), dict(path='removed.py', mode='100644', content=None)]))
        self.branch = 'platform/' + self.task['id'] + '/' + self.change['id']; self.current = None; self.pulls = []; self.requests = []; self.ref_ack_lost = self.pull_ack_lost = False; self.pull_reconciled = True; self.wrong_head = False
        self.protection = dict(required_status_checks=dict(strict=True, contexts=[POLICY['required_check']]), enforce_admins=dict(enabled=True)); self.checks = [dict(name=POLICY['required_check'], status='completed', conclusion='success', app={'slug': 'github-actions'})]
        self.main = self.base; self.pull_state = dict(number=17, html_url='https://github.com/' + self.repository + '/pull/17', head={'sha': self.head}, base={'sha': self.base}, mergeable_state='clean', merged=False); self.merge_confirmed = True
        self.enterContext(patch.object(self.broker, 'request', side_effect=self.request))

    def request(self, method, path, body=None):
        self.requests.append((method, path, copy.deepcopy(body))); suffix = path.removeprefix('/repos/' + self.repository + '/')
        if suffix == 'git/blobs': self.assertEqual(body['encoding'], 'base64'); return {'sha': 'e' * 40}
        if suffix == 'git/commits/' + self.base: return {'tree': {'sha': 'f' * 40}}
        if suffix == 'git/trees': return {'sha': self.tree}
        if suffix == 'git/commits': return {'sha': self.head}
        if suffix == 'git/ref/heads/' + self.branch:
            if self.current is None: raise github.ApiError(404)
            return {'object': {'sha': self.current}}
        if suffix == 'git/refs':
            self.current = body['sha']
            if self.ref_ack_lost: raise github.ApiError(422)
            return {'created': True}
        if suffix.startswith('pulls?'): return copy.deepcopy(self.pulls)
        if suffix == 'pulls':
            if self.pull_reconciled: self.pulls = [dict(self.pull_state, head={'sha': 'changed' if self.wrong_head else self.head})]
            if self.pull_ack_lost: raise github.ApiError(422)
            return self.pulls[0]
        if suffix == 'branches/main/protection': return copy.deepcopy(self.protection)
        if suffix == 'git/ref/heads/main': return {'object': {'sha': self.main}}
        if suffix == 'pulls/17': return copy.deepcopy(self.pull_state)
        if suffix == 'commits/' + self.head + '/check-runs?per_page=100': return {'check_runs': copy.deepcopy(self.checks)}
        if suffix == 'pulls/17/merge':
            self.assertEqual(body, dict(sha=self.head, merge_method='merge'))
            if self.merge_confirmed: self.pull_state.update(merged=True, merge_commit_sha='1' * 40)
            return {'merged': self.merge_confirmed}
        raise AssertionError((method, path, body))

    def test_publication_uses_exact_patch_objects_and_reproducible_commit_without_private_task_details(self):
        original = canonical(self.change); result = self.broker.publish(self.task, self.change); self.assertEqual(result, dict(head_sha=self.head, pr_number=17, pr_url=self.pull_state['html_url'], git_tree_sha=self.tree)); self.assertEqual(canonical(self.change), original)
        blob = next(body for _, path, body in self.requests if path.endswith('/git/blobs')); self.assertEqual(base64.b64decode(blob['content']), b'# Original tested implementation\n')
        tree = next(body for _, path, body in self.requests if path.endswith('/git/trees')); self.assertEqual(tree, dict(base_tree='f' * 40, tree=[dict(path='source.py', mode='100644', type='blob', sha='e' * 40), dict(path='removed.py', mode='100644', type='blob', sha=None)]))
        commit = next(body for _, path, body in self.requests if path.endswith('/git/commits')); self.assertEqual(commit['author'], commit['committer']); self.assertEqual(commit['author']['date'], self.task['created_at']); self.assertEqual(commit['parents'], [self.base]); self.assertEqual(commit['tree'], self.tree)
        public = json.dumps([body for method, _, body in self.requests if method == 'POST']); self.assertNotIn(self.task['title'], public); self.assertNotIn(self.task['objective'], public); self.assertIn(self.change['patch']['tree_sha256'], public)
        self.requests.clear(); self.assertEqual(self.broker.publish(self.task, self.change), result); self.assertFalse(any(path.endswith(('/git/refs', '/pulls')) for _, path, _ in self.requests)); repeated = next(body for _, path, body in self.requests if path.endswith('/git/commits')); self.assertEqual(repeated, commit)

    def test_lost_ref_and_pull_acknowledgments_adopt_only_the_same_exact_commit(self):
        self.ref_ack_lost = self.pull_ack_lost = True; self.assertEqual(self.broker.publish(self.task, self.change)['head_sha'], self.head)
        self.assertEqual(sum(path.endswith('/git/refs') for _, path, _ in self.requests), 1); self.assertEqual(sum(path.endswith('/pulls') for _, path, _ in self.requests), 1)
        self.current = '0' * 40
        with self.assertRaisesRegex(Hold, 'branch differs'): self.broker.publish(self.task, self.change)

    def test_ambiguous_pull_lost_ack_and_changed_head_never_become_confirmed_publication(self):
        self.current = self.head; self.pulls = [self.pull_state, self.pull_state]
        with self.assertRaisesRegex(Hold, 'ambiguous pull-request'): self.broker.publish(self.task, self.change)
        self.pulls = []; self.pull_ack_lost = True; self.pull_reconciled = False
        with self.assertRaisesRegex(Hold, 'acknowledgment remains uncertain') as error: self.broker.publish(self.task, self.change)
        self.assertEqual(error.exception.status, 'reconciling')
        self.pull_ack_lost = False; self.pull_reconciled = True; self.wrong_head = True
        with self.assertRaisesRegex(Hold, 'head differs'): self.broker.publish(self.task, self.change)

    def test_nonconflict_service_errors_are_never_retried_as_creation_races(self):
        for ending, status in [('git/ref/heads/' + self.branch, 503), ('git/refs', 503), ('pulls', 503)]:
            original = self.request
            def request(method, path, body=None):
                if path.endswith('/' + ending): raise github.ApiError(status)
                return original(method, path, body)
            self.current = None; self.pulls = []
            with patch.object(self.broker, 'request', side_effect=request), self.subTest(ending=ending), self.assertRaises(github.ApiError) as error: self.broker.publish(self.task, self.change)
            self.assertEqual(error.exception.code, status)

    def test_merge_requires_protected_unchanged_base_clean_pull_and_exact_github_actions_checks(self):
        self.assertTrue(self.broker.ready(self.change))
        for main in ['malformed', '0' * 40]:
            self.main = main
            with self.subTest(main=main), self.assertRaisesRegex(Hold, 'malformed|Main advanced'): self.broker.ready(self.change)
        self.main = self.base
        for field, value in [('head', {'sha': 'changed'}), ('base', {'sha': 'changed'}), ('mergeable_state', 'dirty')]:
            before = copy.deepcopy(self.pull_state); self.pull_state[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(Hold, 'not clean'): self.broker.ready(self.change)
            self.pull_state = before
        for checks in [[], [dict(self.checks[0], status='in_progress')], [dict(self.checks[0], conclusion='failure')], [dict(self.checks[0], app={'slug': 'unrelated-app'})], [dict(self.checks[0], name='Unrelated check')]]:
            self.checks = checks
            with self.subTest(checks=checks), self.assertRaisesRegex(Hold, 'successful Platform validation'): self.broker.ready(self.change)

    def test_branch_protection_requires_admin_enforcement_and_accepts_both_github_context_formats(self):
        self.assertEqual(self.broker.protection(self.repository), self.protection)
        self.protection['required_status_checks'] = dict(strict=True, checks=[{'context': POLICY['required_check']}]); self.assertEqual(self.broker.protection(self.repository), self.protection)
        for value in [None, dict(strict=False, contexts=[POLICY['required_check']]), dict(strict=True, contexts=['other'])]:
            self.protection['required_status_checks'] = value
            with self.subTest(value=value), self.assertRaisesRegex(Hold, 'administrator-enforced'): self.broker.protection(self.repository)
        self.protection = dict(required_status_checks=dict(strict=True, contexts=[POLICY['required_check']]), enforce_admins={'enabled': False})
        with self.assertRaisesRegex(Hold, 'administrator-enforced'): self.broker.protection(self.repository)
        for code in [404, 503]:
            with patch.object(self.broker, 'get', side_effect=github.ApiError(code)), self.subTest(code=code), self.assertRaises(Hold) as error: self.broker.protection(self.repository)
            self.assertIn('Configure strict' if code == 404 else 'HTTP 503', str(error.exception))

    def test_merge_response_and_readback_must_both_confirm_the_tested_head(self):
        self.assertIsNone(self.broker.merge_receipt(self.change)); self.assertEqual(self.broker.merge(self.change), dict(merge_sha='1' * 40, head_sha=self.head, pr_number=17))
        self.pull_state['head']['sha'] = 'changed'
        with self.assertRaisesRegex(Hold, 'head changed after validation'): self.broker.merge_receipt(self.change)
        self.pull_state['head']['sha'] = self.head; self.merge_confirmed = False
        with self.assertRaisesRegex(Hold, 'not confirmed the merge') as error: self.broker.merge(self.change)
        self.assertEqual(error.exception.status, 'reconciling')


if __name__ == '__main__': unittest.main()
