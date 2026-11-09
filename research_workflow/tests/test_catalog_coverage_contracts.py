"""Catalogue pagination, identities and bounded projections fail closed."""
import base64
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import paper_catalog as catalog
import store
import test_paper_catalog as fixtures
from workflow import canonical


class CatalogCoverageContracts(unittest.TestCase):
    def test_cursor_shape_collection_bounds_and_unknown_google_identity_are_explicit(self):
        cursor=base64.urlsafe_b64encode(canonical([])).decode().rstrip('=')
        with self.assertRaises(ValueError):catalog.before_cursor(cursor)
        for value in [{'wrong':'shape'},[{}, {}, {}]]:
            with patch.object(store,'rows',return_value=value),self.assertRaisesRegex(store.StoreError,'coverage is invalid'):catalog.collect('studies',{},2)
        actor=str(uuid.uuid4())
        with patch.object(store,'request',return_value={'id':actor,'identities':{}}):self.assertIsNone(catalog.google_display_name(actor))

    def test_initiator_event_and_profile_coverage_cannot_mix_another_study_or_actor(self):
        study,actor,foreign=(str(uuid.uuid4()) for _ in range(3));event={'study_id':study,'actor_id':actor,'action':'intake'}
        for events in [[event]*101,[dict(event,study_id=foreign)],[dict(event,action='publish')]]:
            with patch.object(catalog,'collect',return_value=events),self.assertRaisesRegex(store.StoreError,'initiator coverage'):catalog.initiator_names([{'id':study}])
        for profiles in [[{'id':foreign}],[{'id':actor,'first_name':'Original','last_name':'Name'}]*2]:
            with patch.object(catalog,'collect',side_effect=[[event],profiles]),self.assertRaisesRegex(store.StoreError,'profile coverage'):catalog.initiator_names([{'id':study}])

    def test_publication_projection_rejects_foreign_or_duplicate_membership_and_oversized_titles(self):
        study=copy.deepcopy(fixtures.fixture()[0]);foreign=str(uuid.uuid4())
        for published in [[{'id':foreign}],[{'id':study['id']}]*2]:
            def collect(kind,params,maximum,public=False):return published if public else [study] if kind=='studies' else []
            with patch.object(catalog,'collect',side_effect=collect),self.assertRaisesRegex(store.StoreError,'publication coverage'):catalog.papers()
        study['title']='x'*200001
        with patch.object(catalog,'collect',side_effect=lambda kind,params,maximum,public=False:[] if public or kind!='studies' else [study]),patch.object(catalog,'prepared_bibliography',return_value={}),self.assertRaisesRegex(store.StoreError,'response budget'):catalog.papers()
        with patch.object(store,'rows',return_value=[dict(study,id=foreign)]),self.assertRaisesRegex(store.StoreError,'title identity'):catalog.title(study['id'])


if __name__=='__main__':unittest.main()
