"""Development regressions for the confirmation/extraction boundary, not live AI evaluation."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import autonomy_model as model, autonomy_sources as sources, paper_uploads, paper_titles, store

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
 def confirmation(self,**changes):
  return dict(action='confirm',request_id='10000000-0000-4000-8000-000000000002',title=TITLE,domain='Networking',revision=4,claim_ids=['10000000-0000-4000-8000-000000000003'],**changes)
 def test_confirmation_passes_ordered_author_edits_only_after_installed_sql_capability(self):
  authors=['Doe, Jane','María José de la Cruz']
  with patch.object(store,'owns',return_value=True),patch.object(store,'rpc',side_effect=[dict(version=paper_titles.REVIEW_AUTHORS_VERSION),{}]) as rpc,patch.object(paper_uploads,'review',return_value={}),patch.object(paper_uploads.autonomy,'wake'):
   paper_uploads.submit('10000000-0000-4000-8000-000000000001',self.confirmation(authors=authors))
  self.assertEqual([call.args[1]['p_operation'] for call in rpc.call_args_list],['authors-capability','confirm'])
  self.assertEqual(rpc.call_args.args[1]['p_payload']['authors'],authors)
  with patch.object(store,'owns',return_value=True),patch.object(store,'rpc',return_value={}) as rpc,self.assertRaisesRegex(ValueError,'unavailable'):
   paper_uploads.submit('10000000-0000-4000-8000-000000000001',self.confirmation(authors=authors))
  self.assertEqual(rpc.call_count,1)
 def test_invalid_author_edits_cannot_begin_confirmation(self):
  for authors in (None,'One Name',[123],[''],['A'*201],['mail@example.org'],['https://example.org'],['Line\nbreak'],['Name']*101):
   with patch.object(store,'owns',return_value=True),patch.object(store,'rpc') as rpc,self.assertRaises(ValueError):
    paper_uploads.submit('10000000-0000-4000-8000-000000000001',self.confirmation(authors=authors))
   rpc.assert_not_called()
 def test_owner_bibliography_does_not_rewrite_extracted_byline_and_can_be_cleared(self):
  extracted=dict(version='paper-authors-v1',names=['Extracted Name'],page=1,method='Synthetic fixture')
  metadata=dict(authors=extracted,owner_authors=dict(version=paper_titles.REVIEW_AUTHORS_VERSION,names=['Doe, Jane','María José de la Cruz']))
  original=copy.deepcopy(metadata)
  self.assertEqual(paper_titles.review_authors(metadata),metadata['owner_authors']['names']);self.assertEqual(metadata,original)
  metadata['owner_authors']['names']=[];self.assertIsNone(paper_titles.review_authors(metadata))
  self.assertEqual(paper_titles.review_authors(dict(authors=extracted)),['Extracted Name'])

if __name__=='__main__':unittest.main()
