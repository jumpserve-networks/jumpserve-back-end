"""Prompt browsing preserves templates and history without author/contact access."""
import copy
import json
from pathlib import Path
import sys
import unittest
import urllib.parse
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import api
import prompt_catalog as catalog
import store

ACTOR = '11111111-1111-4111-8111-111111111111'


class PromptCatalog(unittest.TestCase):
    def setUp(self):
        self.prompts = [dict(id=f'20000000-0000-4000-8000-{i:012}', module_id='research-autonomy', version=f'prompt-{i}',
            content_sha256='a'*64, created_at='2026-11-01T00:00:00Z', updated_at='2026-11-01T00:00:00Z',
            published_at='2026-11-01T00:00:00Z' if i < 3 else None, system_prompt='Exact template <script>text</script>',
            research_context='Exact context', created_by='private@example.org', updated_by='private@example.org') for i in range(1, 7)]
        self.settings = [dict(module_id='research-autonomy', active_version_id=self.prompts[1]['id'])]
        self.calls = []
        self.patcher = patch.object(store, 'request', side_effect=self.read)
        self.patcher.start(); self.addCleanup(self.patcher.stop)

    def read(self, path, **kwargs):
        self.assertEqual(kwargs, {})  # GET only, never an update or RPC.
        self.calls.append(path)
        url = urllib.parse.urlsplit(path); params = dict(urllib.parse.parse_qsl(url.query))
        self.assertIn(url.path, ('/rest/v1/agent_prompt_versions', '/rest/v1/agent_prompt_settings'))
        rows = self.prompts if url.path.endswith('versions') else self.settings
        self.assertNotIn('created_by', params['select']); self.assertNotIn('updated_by', params['select'])
        if params.get('id'): rows = [row for row in rows if row['id'] == params['id'][3:]]
        offset = int(params.get('offset', 0)); limit = min(2, int(params['limit']))  # Actual API may cap a page.
        return [{k: copy.deepcopy(row[k]) for k in params['select'].split(',')} for row in rows[offset:offset+limit]]

    def event(self, path, token=False, method='GET'):
        return dict(rawPath=path, headers={'Authorization':'Bearer synthetic-token'} if token else {}, requestContext={'http':{'method':method}})

    def test_all_versions_across_short_pages_include_drafts_and_exact_active_pointer(self):
        result = catalog.index()
        self.assertEqual(len(result['prompts']), 6)
        self.assertEqual([p['id'] for p in result['prompts'] if p['active']], [self.prompts[1]['id']])
        self.assertIsNone(result['prompts'][-1]['published_at'])
        self.assertNotIn('system_prompt', json.dumps(result)); self.assertNotIn('private@example.org', json.dumps(result))
        self.assertGreater(len(self.calls), 4)

    def test_detail_returns_exact_content_without_audit_accounts(self):
        result = catalog.detail(self.prompts[0]['id'])['prompt']
        self.assertEqual(result['system_prompt'], self.prompts[0]['system_prompt'])
        self.assertEqual(result['research_context'], self.prompts[0]['research_context'])
        self.assertFalse(result['active'])
        self.assertNotIn('created_by', result)
        self.assertIsNone(catalog.detail('20000000-0000-4000-8000-999999999999'))
        with self.assertRaises(ValueError): catalog.detail('not-a-version')

    def test_unauthenticated_requests_do_not_read_database(self):
        for path in ['/research/prompts', '/research/prompts/'+self.prompts[0]['id']]:
            self.assertEqual(api.handler(self.event(path))['statusCode'], 401)
        self.assertEqual(self.calls, [])
        with patch.object(api, 'authenticate', side_effect=api.HttpError(403, 'Google account required.')):
            self.assertEqual(api.handler(self.event('/research/prompts', token=True))['statusCode'], 403)
        self.assertEqual(self.calls, [])

    def test_authenticated_routes_are_no_store_and_do_not_allow_writes(self):
        with patch.object(api, 'authenticate', return_value=ACTOR):
            index = api.handler(self.event('/research/prompts', token=True))
            self.assertEqual(index['statusCode'], 200)
            self.assertEqual(index['headers']['Cache-Control'], 'no-store')
            detail = api.handler(self.event('/research/prompts/'+self.prompts[0]['id'], token=True))
            self.assertEqual(detail['statusCode'], 200)
            self.assertEqual(api.handler(self.event('/research/prompts/20000000-0000-4000-8000-999999999999', token=True))['statusCode'], 404)
            before = len(self.calls)
            for method in ['POST', 'DELETE', 'PATCH']:
                self.assertEqual(api.handler(self.event('/research/prompts', token=True, method=method))['statusCode'], 404)
            self.assertEqual(len(self.calls), before)

    def test_catalogue_limit_does_not_silently_omit_versions(self):
        with patch.object(catalog, 'MAX_VERSIONS', 3):
            with self.assertRaises(store.StoreError): catalog.index()


if __name__ == '__main__': unittest.main()
