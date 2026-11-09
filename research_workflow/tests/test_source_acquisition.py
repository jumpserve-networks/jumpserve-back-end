"""Pinned retrieval and retained PDF acquisition with synthetic network/parser IO."""
import io
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import MagicMock, patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import autonomy_sources as sources
import store
from workflow import canonical, digest, validate_record

def uid(): return str(uuid.uuid4())


class Response:
    def __init__(self, body=b'%PDF-Synthetic original', status=200, headers=None):
        self.status = status; self.body = io.BytesIO(body); self.headers = {'Content-Type': 'application/pdf', **(headers or {})}; self.sizes = []
    def getheader(self, name): return self.headers.get(name)
    def read1(self, maximum): self.sizes.append(maximum); return self.body.read(maximum)


class SourceAcquisition(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(sources.socket, 'getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))]))
        self.enterContext(patch.object(sources.time, 'monotonic', return_value=0)); self.responses = [Response()]; self.connections = []; self.no_socket = False; self.pinned_https = sources.PinnedHTTPS
        test = self
        class Connection:
            def __init__(self, host, ip, timeout):
                test.assertEqual((ip, timeout), ('8.8.8.8', 10)); self.host = host; self.sock = None if test.no_socket else MagicMock(); self.response = test.responses.pop(0); self.requests = []; self.closed = False; test.connections.append(self)
            def request(self, *args, **options): self.requests.append((args, options))
            def getresponse(self): return self.response
            def close(self): self.closed = True
        self.enterContext(patch.object(sources, 'PinnedHTTPS', Connection))
        self.study = dict(id=uid(), title='Synthetic retained source', paper_url='https://example.test/paper'); self.actor = uid(); self.artifacts = []; self.rows = self.enterContext(patch.object(store, 'rows', return_value=[])); self.append = self.enterContext(patch.object(store, 'append', side_effect=self.validate_append)); self.retain = self.enterContext(patch.object(store, 'artifact', side_effect=self.artifact))
    def artifact(self, study, actor, raw, identity, media, provenance):
        self.assertEqual((study, actor), (self.study['id'], self.actor)); item = dict(id=identity, sha256=digest(raw), byte_count=len(raw), storage_path=study + '/' + digest(raw), media_type=media, provenance=provenance, verified_at='2026-11-01T00:00:00Z'); self.artifacts.append((item, raw)); return item
    def validate_append(self, study, actor, kind, record): self.assertEqual((study, actor), (self.study['id'], self.actor)); validate_record(kind, record); return record
    def extract(self, source, target):
        self.assertTrue(Path(source).read_bytes().lstrip().startswith(b'%PDF-')); Path(target).write_bytes(canonical(dict(pages=[dict(page=1, text='Synthetic extracted text')], document_metadata={'title': None})))

    def test_retrieval_checks_every_resolved_address_and_pins_tls_to_the_validated_ip(self):
        self.assertEqual(sources.address('https://example.test/paper')[1], '8.8.8.8')
        for records in [[], [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))], [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443)), (socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('::1', 443, 0, 0))]]:
            with patch.object(sources.socket, 'getaddrinfo', return_value=records), self.subTest(records=records), self.assertRaisesRegex(ValueError, 'Private, local'): sources.address('https://example.test/paper')
        for url in ['http://example.test/paper', 'https://example.test:8443/paper', 'https://example.test/paper#fragment', 'https://example.local/paper']:
            with self.subTest(url=url), self.assertRaises(ValueError): sources.address(url)
        # Instantiate the actual class after temporarily restoring that one adapter.
        with patch.object(sources, 'PinnedHTTPS', self.pinned_https):
            connection = sources.PinnedHTTPS('example.test', '8.8.8.8', 4); context = MagicMock(); sock = MagicMock()
            with patch.object(connection, '_context', context), patch.object(sources.socket, 'create_connection', return_value=sock) as connect: connection.connect()
            connect.assert_called_once_with(('8.8.8.8', 443), 4); context.wrap_socket.assert_called_once_with(sock, server_hostname='example.test'); self.assertIs(connection.sock, context.wrap_socket.return_value)

    def test_pdf_response_keeps_original_bytes_query_path_and_per_attempt_audit(self):
        raw, url, audit = sources.fetch('https://example.test/paper?version=1'); self.assertEqual(raw, b'%PDF-Synthetic original'); self.assertEqual(url, 'https://example.test/paper?version=1'); self.assertEqual(audit[0]['status_code'], 200); self.assertEqual(audit[0]['media_type'], 'application/pdf'); self.assertTrue(self.connections[0].closed); self.assertEqual(self.connections[0].requests[0][0], ('GET', '/paper?version=1')); self.assertEqual(self.connections[0].requests[0][1]['headers']['Accept-Encoding'], 'identity')
        self.responses = [Response(headers={'Content-Length': str(len(raw))})]; self.connections.clear(); self.no_socket = True
        second, _, _ = sources.fetch('https://example.test'); self.assertEqual(self.connections[0].requests[0][0], ('GET', '/')); self.assertEqual(second, raw)

    def test_redirects_are_revalidated_and_failures_keep_prior_attempts_without_following_private_addresses(self):
        self.responses = [Response(status=302, headers={'Location': '/version.pdf'}), Response()]; raw, url, audit = sources.fetch(self.study['paper_url']); self.assertEqual(url, 'https://example.test/version.pdf'); self.assertEqual([row['status_code'] for row in audit], [302, 200]); self.assertTrue(all(c.closed for c in self.connections))
        self.responses = [Response(status=302, headers={'Location': 'https://private.example.test/private'})]
        def addresses(hostname, *args, **options): return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1' if hostname == 'private.example.test' else '8.8.8.8', 443))]
        with patch.object(sources.socket, 'getaddrinfo', side_effect=addresses), self.assertRaises(ValueError) as error: sources.fetch(self.study['paper_url'])
        self.assertEqual(len(error.exception.retrieval_attempts), 1); self.assertEqual(error.exception.retrieval_attempts[0]['status_code'], 302); self.assertEqual(error.exception.captured_response, b''); self.assertEqual(len(self.responses), 0)
        self.responses = [Response(status=302, headers={'Location': '/again'}) for _ in range(6)]
        with self.assertRaisesRegex(ValueError, 'redirect limit') as error: sources.fetch(self.study['paper_url'])
        self.assertEqual(len(error.exception.retrieval_attempts), 6)

    def test_invalid_status_missing_redirect_oversized_or_incomplete_body_retains_bounded_prefix(self):
        cases = [(Response(status=302), 'omitted its destination'), (Response(status=503), 'HTTP 503'), (Response(headers={'Content-Length': str(sources.MAX_BYTES + 1)}), '10 MB'), (Response(body=b'%PDF-partial', headers={'Content-Length': '100'}), 'Incomplete'), (Response(body=b'x' * (sources.MAX_BYTES + 1)), 'oversized')]
        for response, message in cases:
            self.responses = [response]
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message) as error: sources.fetch(self.study['paper_url'])
            self.assertEqual(error.exception.retrieval_attempts[0]['url'], self.study['paper_url']); self.assertLessEqual(len(error.exception.captured_response), sources.MAX_BYTES + 1); self.assertTrue(self.connections[-1].closed)

    def test_wall_time_failure_before_connect_or_after_partial_read_does_not_silently_return_truncated_bytes(self):
        with patch.object(sources.time, 'monotonic', side_effect=[0, 36]), self.assertRaisesRegex(ValueError, 'wall-time'): sources.fetch(self.study['paper_url'])
        self.assertFalse(self.connections)
        self.responses = [Response(body=b'x' * 70000)]
        with patch.object(sources.time, 'monotonic', side_effect=[0, 0, 0, 36]), self.assertRaisesRegex(ValueError, 'wall-time') as error: sources.fetch(self.study['paper_url'])
        self.assertEqual(len(error.exception.captured_response), 65536); self.assertEqual(len(error.exception.retrieval_attempts), 1); self.assertTrue(self.connections[-1].closed)

    def test_public_landing_pdf_and_derived_text_have_separate_hash_bound_originals_and_no_full_review_claim(self):
        landing = b'<meta name="citation_pdf_url" content="/paper.pdf"><meta name="unrelated" content="ignored">'; self.responses = [Response(body=landing, headers={'Content-Type': 'text/html'}), Response()]
        with patch.object(sources.intake, 'text_packet', side_effect=self.extract): result = sources.original(self.study, self.actor)
        self.assertEqual([record['provenance']['kind'] for record, raw in self.artifacts], ['paper-landing-original', 'paper-original', 'derived-pdf-text']); self.assertEqual(self.artifacts[0][1], landing); self.assertEqual(self.artifacts[1][1], b'%PDF-Synthetic original'); self.assertEqual(result['original_artifact_id'], self.artifacts[1][0]['id']); self.assertEqual(result['text_artifact_id'], self.artifacts[2][0]['id'])
        source = next(call.args[3] for call in self.append.call_args_list if call.args[2] == 'sources'); self.assertEqual(source['review_status'], 'retrieved-unreviewed'); self.assertEqual(source['retrieved_url'], 'https://example.test/paper.pdf'); self.assertEqual(len(source['retrieval_attempts']), 2); self.assertIn('do not establish complete review', source['review_definition']); self.assertEqual(source['sha256'], digest(self.artifacts[1][1]))

    def test_verified_private_upload_reuses_original_artifact_without_an_external_fetch(self):
        raw = b'%PDF-Synthetic private upload'; identity = uid(); self.study.update(paper_url=None, paper_upload=dict(sha256=digest(raw), byte_count=len(raw), filename='synthetic.pdf')); record = dict(id=identity, sha256=digest(raw), provenance={'kind': 'paper-original-upload'})
        self.rows.return_value = [record]
        with patch.object(store, 'artifact_bytes', return_value=raw), patch.object(sources.intake, 'text_packet', side_effect=self.extract), patch.object(sources, 'fetch') as fetch: result = sources.original(self.study, self.actor)
        fetch.assert_not_called(); self.assertEqual(result['original_artifact_id'], identity); self.assertEqual(self.artifacts[0][0]['provenance']['original_sha256'], digest(raw)); source = next(call.args[3] for call in self.append.call_args_list if call.args[2] == 'sources'); self.assertIsNone(source['source_url']); self.assertEqual(source['review_status'], 'retrieved-unreviewed')
        for rows, data, message in [([], raw, 'not verified and retained'), ([dict(record, provenance={'kind': 'other'})], raw, 'not verified and retained'), ([record], b'Changed original', 'differs from the uploaded')]:
            self.rows.return_value = rows
            with patch.object(store, 'artifact_bytes', return_value=data), self.subTest(message=message), self.assertRaisesRegex(ValueError, message): sources.original(self.study, self.actor)

    def test_landing_and_extraction_failures_retain_originals_and_never_create_a_successful_text_packet(self):
        for body, second in [(b'<html>No PDF URL</html>', None), (b'<meta name="citation_pdf_url" content="/not-pdf">', b'Not a PDF')]:
            self.responses = [Response(body=body)] + ([Response(body=second)] if second else [])
            with self.assertRaisesRegex(ValueError, 'accessible PDF|not a full-text PDF'): sources.original(self.study, self.actor)
        self.responses = [Response()]
        def fail(source, target): Path(str(target) + '.failure.json').write_bytes(b'{"original_failure":"Synthetic parser error"}'); raise RuntimeError('Synthetic parser error')
        with patch.object(sources.intake, 'text_packet', side_effect=fail), self.assertRaisesRegex(RuntimeError, 'parser error'): sources.original(self.study, self.actor)
        failures = [(record, raw) for record, raw in self.artifacts if record['provenance']['kind'] == 'failed-text-extraction']; self.assertEqual(len(failures), 1); self.assertEqual(failures[0][1], b'{"original_failure":"Synthetic parser error"}'); self.assertFalse(any(record['provenance']['kind'] == 'derived-pdf-text' for record, raw in self.artifacts))

    def test_partial_retrieval_is_retained_as_failed_prefix_and_never_as_a_complete_paper(self):
        raw = b'%PDF-original partial bytes'; self.responses = [Response(body=raw, headers={'Content-Length': '100'})]
        with self.assertRaisesRegex(ValueError, 'Incomplete'): sources.original(self.study, self.actor)
        self.assertEqual(len(self.artifacts), 1); record, retained = self.artifacts[0]; self.assertEqual(retained, raw); self.assertEqual(record['provenance']['kind'], 'failed-retrieval-response-prefix'); self.assertIn('not full-paper bytes', record['provenance']['transformation'])
        source = next(call.args[3] for call in self.append.call_args_list if call.args[2] == 'sources'); self.assertEqual(source['review_status'], 'unavailable-full-text'); self.assertIsNone(source['sha256']); self.assertIsNone(source['byte_count']); self.assertEqual(source['retrieval_attempts'][0]['status_code'], 200); self.assertEqual(source['retrieval_attempts'][-1]['status'], 'failed')

    def test_failed_extraction_without_a_diagnostic_file_does_not_invent_one_or_a_text_packet(self):
        with patch.object(sources.intake, 'text_packet', side_effect=RuntimeError('Synthetic immediate extraction failure')), self.assertRaisesRegex(RuntimeError, 'immediate extraction'): sources.original(self.study, self.actor)
        self.assertEqual([record['provenance']['kind'] for record, raw in self.artifacts], ['paper-original'])

    def test_blank_pages_and_multichunk_source_offsets_do_not_invent_claim_quotes(self):
        packet = {'pages': [{'page': 1, 'text': ' \n'}, {'page': 2, 'text': 'α' * (sources.CHUNK_CHARACTERS + 1)}]}; original = canonical(packet); chunks = sources.chunks(packet); self.assertEqual([(c['page'], c['offset'], len(c['text'])) for c in chunks], [(2, 0, sources.CHUNK_CHARACTERS), (2, sources.CHUNK_CHARACTERS, 1)]); self.assertEqual(''.join(c['text'] for c in chunks), packet['pages'][1]['text']); self.assertEqual(canonical(packet), original)
        for claim in [dict(page=1, quote='α', claim_type='numerical'), dict(page=2, quote='Not in the source', claim_type='numerical')]:
            with self.subTest(claim=claim), self.assertRaisesRegex(ValueError, 'exact supplied source'): sources.candidates(self.study['id'], uid(), chunks[0], [claim])


if __name__ == '__main__': unittest.main()
