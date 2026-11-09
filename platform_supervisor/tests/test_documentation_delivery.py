"""Context7 protocol and exact-byte delivery with synthetic MCP transport."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from platform_supervisor import documentation as docs
from platform_supervisor.common import canonical, digest, Hold


class Response(io.BytesIO):
    def __init__(self, raw, headers=None): super().__init__(raw); self.headers = headers or {}


class Context7Transport(unittest.TestCase):
    def setUp(self):
        self.opener = MagicMock(); self.enterContext(patch.object(docs, 'network_opener', return_value=self.opener)); self.client = docs.Context7('synthetic-documentation-token')
    def test_initialize_and_notifications_keep_negotiated_protocol_session_and_credentials_in_headers(self):
        self.opener.open.side_effect = [Response(canonical({'id': 1, 'result': {'protocolVersion': '2025-06-18'}}), {'Mcp-Session-Id': 'synthetic-session'}), Response(b'')]; self.client.initialize(); self.assertEqual(self.client.protocol, '2025-06-18'); self.assertEqual(self.client.session, 'synthetic-session'); requests = [call.args[0] for call in self.opener.open.call_args_list]
        self.assertEqual(json.loads(requests[0].data)['method'], 'initialize'); notification = json.loads(requests[1].data); self.assertEqual(notification['method'], 'notifications/initialized'); self.assertNotIn('id', notification); self.assertEqual(requests[1].get_header('Mcp-session-id'), 'synthetic-session'); self.assertEqual(requests[1].get_header('Mcp-protocol-version'), '2025-06-18'); self.assertEqual(requests[1].get_header('Authorization'), 'Bearer synthetic-documentation-token'); self.assertEqual(requests[1].full_url, 'https://mcp.context7.com/mcp'); self.assertEqual(self.opener.open.call_args.kwargs, {'timeout': 30}); self.assertNotIn('synthetic-documentation-token', requests[1].full_url)
    def test_event_stream_uses_only_matching_request_id_and_refreshes_callable_credentials(self):
        key = MagicMock(return_value='synthetic-rotated-token'); client = docs.Context7(key); raw = b': heartbeat\nevent: message\ndata: {"id":999,"result":{"wrong":true}}\ndata: {"id":1,"result":{"content":[{"text":"Original primary documentation"}]}}\n'
        self.opener.open.return_value = Response(raw, {'Content-Type': 'text/event-stream'}); self.assertEqual(client.request('tools/call', {'query': 'Public topic'}), {'content': [{'text': 'Original primary documentation'}]}); key.assert_called_once(); self.assertEqual(self.opener.open.call_args.args[0].get_header('Authorization'), 'Bearer synthetic-rotated-token')
    def test_missing_error_oversized_and_redirected_responses_never_become_documentation(self):
        for raw, headers in [(b'{}', {}), (canonical({'id': 1, 'error': {'message': 'Synthetic failure'}}), {}), (b'data: {"id":999,"result":{}}\n', {'Content-Type': 'text/event-stream'})]:
            self.opener.open.return_value = Response(raw, headers)
            with self.subTest(raw=raw), self.assertRaisesRegex(Hold, 'no successful documentation response'): self.client.request('tools/call', {})
        self.opener.open.return_value = Response(b'x' * 1000001)
        with self.assertRaisesRegex(Hold, 'bounded delivery'): self.client.request('tools/call', {})
        with self.assertRaisesRegex(Hold, 'credential redirects are disabled'): docs.NoRedirect().redirect_request(None, None, None, None, None, None)
    def test_library_resolution_is_fixed_to_primary_library_and_public_bounded_topics(self):
        self.client.request = MagicMock(side_effect=[{'content': [{'text': '/vercel/next.js'}]}, {'content': [{'text': 'Primary docs'}]}]); result = self.client.query('Next.js', 'App Router cookies'); self.assertEqual(result['library_id'], '/vercel/next.js'); self.assertEqual(self.client.request.call_args_list[0].args[1]['arguments'], {'libraryName': 'Next.js', 'query': 'App Router cookies'}); self.assertEqual(self.client.request.call_args_list[1].args[1]['arguments'], {'libraryId': '/vercel/next.js', 'query': 'App Router cookies'})
        for name, topic in [('Unknown library', 'Topic'), ('Next.js', 'private@example.test'), ('Next.js', 'x' * 241), ('Next.js', {}), ('Next.js', '`arbitrary source`')]:
            with self.subTest(name=name, topic=topic), self.assertRaisesRegex(Hold, 'bounded public library'): self.client.query(name, topic)
        for responses in [[{'isError': True}], [{'content': [{'text': '/wrong/library'}]}], [{'content': [{'text': '/vercel/next.js'}]}, {'isError': True}]]:
            self.client.request.side_effect = responses
            with self.subTest(responses=responses), self.assertRaisesRegex(Hold, 'expected primary library|temporarily unavailable'): self.client.query('Next.js', 'App Router cookies')
    def test_anonymous_context7_requests_do_not_invent_authentication_or_session_headers(self):
        self.opener.open.return_value = Response(canonical({'id': 1, 'result': {'content': []}})); client = docs.Context7(); self.assertEqual(client.request('tools/call', {}), {'content': []}); request = self.opener.open.call_args.args[0]; self.assertIsNone(request.get_header('Authorization')); self.assertIsNone(request.get_header('Mcp-session-id'))


class DocumentationDelivery(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='context7-delivery-unit-'); self.addCleanup(temporary.cleanup); self.workspace = Path(temporary.name); self.state = {}; self.blobs = {}; self.artifacts = []; self.store = MagicMock(); self.store.task = dict(id=str(uuid.uuid4()), repositories=['jumpserve-networks/jumpserve-front-end', 'jumpserve-networks/jumpserve-infra'], objective='PRIVATE-RESEARCH-OBJECTIVE-CANARY'); self.store.artifact.side_effect = self.artifact; self.store.request.side_effect = lambda path, **options: self.blobs[path.split('/')[-1]]; self.client = MagicMock(); self.client.query.side_effect = lambda library, topic: dict(library=library, topic=topic, primary_documentation={'retained_zero': 0, 'missing': None})
    def artifact(self, name, raw):
        sha = digest(raw); self.blobs[sha] = raw; self.artifacts.append((name, raw)); return dict(id=str(uuid.uuid4()), sha256=sha, storage_path=self.store.task['id'] + '/' + sha)
    def deliver(self): return docs.deliver(self.store, self.workspace, self.state, self.client)
    def test_default_library_delivery_initializes_once_and_preserves_hash_bound_originals_without_private_objective(self):
        self.deliver(); self.client.initialize.assert_called_once(); self.assertEqual({call.args[0] for call in self.client.query.call_args_list}, {'Next.js', 'Supabase', 'AWS CDK'}); cache = self.state['documentation']; self.assertEqual(len(cache), 3)
        for key, pointer in cache.items():
            path = self.workspace / 'references/context7' / (key + '.json'); self.assertEqual(path.read_bytes(), self.blobs[pointer['sha256']]); self.assertEqual(digest(path.read_bytes()), pointer['sha256']); self.assertEqual(path.stat().st_mode & 0o777, 0o600); self.assertEqual(json.loads(path.read_bytes())['primary_documentation'], {'retained_zero': 0, 'missing': None})
        self.assertNotIn('PRIVATE-RESEARCH-OBJECTIVE-CANARY', json.dumps(self.client.query.call_args_list, default=str)); index = json.loads((self.workspace / 'references/context7/index.json').read_bytes()); self.assertEqual(set(index['references']), set(cache)); self.assertIn('not authority', index['instructions'])
    def test_fresh_cache_is_read_back_without_network_and_changed_originals_are_rejected(self):
        self.deliver(); self.client.reset_mock(); original = dict(self.state['documentation']); self.deliver(); self.client.initialize.assert_not_called(); self.client.query.assert_not_called(); self.assertEqual(self.state['documentation'], original); self.assertEqual(self.store.request.call_count, 3)
        key = next(iter(original)); pointer = original[key]; self.blobs[pointer['sha256']] = b'Changed retained documentation'
        with self.assertRaisesRegex(Hold, 'documentation hash differs'): self.deliver()
        self.client.query.assert_not_called()
    def test_expired_cache_and_explicit_public_topics_are_refetched_once_without_discarding_originals(self):
        self.deliver(); originals = dict(self.blobs)
        for pointer in self.state['documentation'].values(): pointer['created_at'] = '2000-01-01T00:00:00+00:00'
        self.client.reset_mock(); (self.workspace / 'documentation-requests.json').write_bytes(canonical([{'library': 'Boto3', 'topic': 'Instance roles refresh'}, {'library': 'Supabase', 'topic': docs.LIBRARIES['Supabase'][1]}])); self.deliver(); self.client.initialize.assert_called_once(); self.assertEqual(self.client.query.call_count, 4); self.assertTrue(set(originals) <= set(self.blobs)); self.assertEqual(len(self.state['documentation']), 4)
    def test_invalid_requests_are_retained_whole_and_feedback_does_not_block_default_public_documentation(self):
        for value in [{'not': 'an array'}, [{'library': 'Unregistered', 'topic': 'Unknown'}], [{'library': 'Next.js', 'topic': 'private@example.test'}], [{'library': 'Next.js', 'topic': 'Public topic', 'source': 'Private code'}], [{'library': 'Supabase', 'topic': 'Public topic'}] * 4]:
            raw = canonical(value); (self.workspace / 'documentation-requests.json').write_bytes(raw); self.deliver(); retained = self.workspace / ('invalid-documentation-requests-' + digest(raw)[:12] + '.json'); self.assertEqual(retained.read_bytes(), raw); self.assertFalse((self.workspace / 'documentation-requests.json').exists()); self.assertIn('Original invalid requests are retained', self.state['feedback'])
        invalid = [raw for name, raw in self.artifacts if name == 'invalid-documentation-requests.json']; self.assertEqual(len(invalid), 5)
    def test_symlink_or_oversized_request_is_refused_before_reading_or_sending_private_contents(self):
        original = self.workspace / 'original-private'; original.write_bytes(b'PRIVATE-DO-NOT-READ'); requests = self.workspace / 'documentation-requests.json'; requests.symlink_to(original)
        with self.assertRaisesRegex(Hold, 'requests exceed their bound'): self.deliver()
        self.client.query.assert_not_called(); self.assertEqual(original.read_bytes(), b'PRIVATE-DO-NOT-READ'); requests.unlink(); requests.write_bytes(b'x' * 4001)
        with self.assertRaisesRegex(Hold, 'requests exceed their bound'): self.deliver()
        self.client.query.assert_not_called()
    def test_default_client_is_created_only_when_caller_does_not_supply_one(self):
        self.store.task['repositories'] = ['jumpserve-networks/jumpserve-back-end']
        with patch.object(docs, 'Context7', return_value=self.client) as client: docs.deliver(self.store, self.workspace, self.state)
        client.assert_called_once_with(); self.assertEqual(self.client.query.call_args.args, ('Supabase', docs.LIBRARIES['Supabase'][1])); self.assertEqual(len(self.state['documentation']), 1)


if __name__ == '__main__': unittest.main()
