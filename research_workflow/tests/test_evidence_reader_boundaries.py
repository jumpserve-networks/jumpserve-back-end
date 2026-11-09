"""Complete evidence readers never turn bounds or incomplete pagination into findings."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import autonomy_evidence as evidence
from workflow import canonical,digest


class EvidenceReaderBoundaries(unittest.TestCase):
    def test_collection_requires_sorted_unique_forward_pages_and_the_requested_page_bound(self):
        for pages,message in [([[{'id':'b'},{'id':'a'}]],'identity order'),([[{'id':'a'},{'id':'a'}]],'identity order'),([[{'id':'a'}],[{'id':'a'}]],'identity order'),([[{'id':str(i)} for i in range(evidence.PAGE_SIZE+1)]],'page size')]:
            reader=iter(pages)
            with self.subTest(message=message),self.assertRaisesRegex(ValueError,message):evidence.collect(lambda *args,**kwargs:next(reader),'measurements','original-study')
        records=[{'id':'a','status':'recorded','metric':'latency','units':'ms','value':0},{'id':'b','status':'failed','metric':'latency','units':'ms','value':None}];queries=[]
        def read(kind,study,**query):queries.append(query);return [] if 'and' in query else records
        self.assertEqual(evidence.collect(read,'measurements','original-study'),records);self.assertEqual(queries[-1]['and'],'(id.gt.b)');manifest=evidence.manifest(records);self.assertEqual(manifest['records_sha256'],digest(canonical(records)));self.assertEqual(manifest['status_counts'],{'recorded':1,'failed':1})

    def test_collection_stops_at_either_count_or_byte_budget_without_returning_a_partial_inventory(self):
        for count,maximum in [(1,10000),(1000,1)]:
            with patch.object(evidence,'MAX_RECORDS',count),patch.object(evidence,'MAX_BYTES',maximum),self.assertRaisesRegex(ValueError,'no data were truncated'):evidence.collect(lambda *args,**kwargs:[{'id':'a'},{'id':'b'}],'measurements','original-study')

    def test_pages_validate_exact_cursors_and_bytes_and_return_explicit_subsets(self):
        records=[{'id':'a','original':'x'*100},{'id':'b','original':'x'*100}]
        for offset in [-1,True,3]:
            with self.subTest(offset=offset),self.assertRaisesRegex(ValueError,'cursor'):evidence.page(records,offset)
        for budget in [0,True,evidence.READ_BYTES+1]:
            with self.subTest(budget=budget),self.assertRaisesRegex(ValueError,'byte budget'):evidence.page(records,0,budget)
        with self.assertRaisesRegex(ValueError,'original private artifact'):evidence.page(records,0,1)
        single=evidence.page(records,0,500);self.assertEqual(single['returned_records'],1);self.assertEqual(single['next_cursor'],1);self.assertFalse(single['complete']);self.assertEqual(single['page_sha256'],digest(canonical(records[:1])));self.assertEqual(evidence.page(records,2)['records'],[])

    def test_protocol_fragments_bind_the_complete_original_unicode_document_and_reject_outside_cursors(self):
        document={'original':'Ω'*2000,'zero':0,'missing':None};original=canonical(document)
        for cursor in [-1,True,len(original.decode())+1]:
            with self.subTest(cursor=cursor),self.assertRaisesRegex(ValueError,'Protocol cursor'):evidence.document_page(document,cursor)
        result=evidence.document_page(document,1500);self.assertEqual(result['document_sha256'],digest(original));self.assertEqual(result['fragment'],original.decode()[1500:3000]);self.assertIsNone(result['next_cursor']);self.assertEqual(result['fragment_sha256'],digest(result['fragment'].encode()))


if __name__=='__main__':unittest.main()
