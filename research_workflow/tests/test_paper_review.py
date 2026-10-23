"""Development regressions for the confirmation/extraction boundary, not live AI evaluation."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import autonomy_model as model, autonomy_sources as sources, paper_uploads, store

TITLE='Cache observations in controlled networks'
TEXT=TITLE+'\nNetworking experiments compare latency.'
CHUNK=dict(page=1,text=TEXT,offset=0)
PACKET=dict(pages=[dict(page=1,text=TEXT)],document_metadata=dict(title=TITLE))
VALUES=dict(title=dict(text=TITLE,quote=TITLE,page=1),domain=dict(text='Computer networking',quote='Networking experiments compare latency.',page=1))
class PaperReview(unittest.TestCase):
 def test_suggestions_preserve_exact_grounding_and_domain_as_a_model_suggestion(self):
  original=copy.deepcopy(VALUES);value=sources.paper_metadata(PACKET,CHUNK,VALUES)
  self.assertEqual(value['title'],TITLE);self.assertEqual(value['domain'],'Computer networking');self.assertEqual(value['suggestions'],original);self.assertEqual(VALUES,original)
 def test_false_page_quote_invented_title_and_bad_types_are_rejected(self):
  for key,change in [('title',dict(page=2)),('domain',dict(quote='Not in the paper')),('title',dict(text='An invented paper title')),('title',dict(text='filename.pdf',quote='filename.pdf')),('domain',dict(page=True)),('domain',dict(text=123))]:
   values=copy.deepcopy(VALUES);values[key].update(change)
   with self.assertRaises(ValueError):sources.paper_metadata(PACKET,CHUNK,values)
 def test_unknown_metadata_stays_missing_and_metadata_needs_corroboration(self):
  unknown=dict(pages=[dict(page=1,text='')],document_metadata=dict(title='Filename that is not a title'))
  value=sources.paper_metadata(unknown,dict(page=1,text='',offset=0),dict(title=None,domain=None))
  self.assertIsNone(value['title']);self.assertIsNone(value['domain']);self.assertIsNone(value['title_fallback'])
 def test_corroborated_title_fallback_does_not_invent_a_domain(self):
  value=sources.paper_metadata(PACKET,CHUNK,dict(title=None,domain=None))
  self.assertEqual(value['title'],TITLE);self.assertIsNone(value['domain']);self.assertEqual(value['title_fallback']['page'],1)
 def test_only_discovery_requests_add_metadata_without_mutating_other_schemas(self):
  requested=model.request('System',dict(task='discover',paper_metadata_requested=True));other=model.request('System',dict(task='discover'))
  schema=lambda r:r['toolConfig']['tools'][0]['toolSpec']['inputSchema']['json']
  self.assertIn('paper_metadata',schema(requested)['required']);self.assertNotIn('paper_metadata',schema(other)['properties']);self.assertNotIn('paper_metadata',model.SCHEMA['properties'])
  with self.assertRaises(ValueError):model.request('System',dict(task='planning',paper_metadata_requested=True))
 def test_owner_checked_before_review_reads_or_confirmation_writes(self):
  for action in ('review','confirm'):
   with patch.object(store,'owns',return_value=False),patch.object(store,'rows') as rows,patch.object(store,'rpc') as rpc,self.assertRaises(ValueError):
    paper_uploads.submit('10000000-0000-4000-8000-000000000001',dict(action=action,request_id='10000000-0000-4000-8000-000000000002'))
   rows.assert_not_called();rpc.assert_not_called()

if __name__=='__main__':unittest.main()
