"""Scientific record invariants: incomplete evidence cannot become a finding."""
import copy
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import workflow
from test_workflow import fixture

IDENTITY = '00000000-0000-4000-8000-000000000001'
REVIEWER = dict(type='human',identity='Synthetic reviewer',independence='Development fixture only')


def records():
    return {
        'sources':dict(id=IDENTITY,citation='Synthetic source',source_url=None,retrieved_url=None,kind='paper',role='primary',retrieved_version=None,sha256=None,byte_count=None,access_status='unavailable',review_status='unavailable-full-text',review_definition='Full text unavailable',examined='Abstract',unexamined='Full text',retrieval_attempts=[],reviewer=REVIEWER,findings='No finding',limitations='No full text'),
        'claims':dict(id=IDENTITY,source_id=IDENTITY,location='Synthetic section',description='Synthetic claim',claim_type='numerical',scope={},metrics=[],priority=1),
        'reviews':dict(id=IDENTITY,scope='scientific',status='passed',reviewer=REVIEWER,judgments={},limitations='Synthetic validation fixture'),
        'campaigns':dict(id=IDENTITY,protocol_id=IDENTITY,followup_of=None,title='Synthetic campaign',stage='pilot',experiment_type='reanalysis',adapter='matched-numeric-v1',planned_units=1,coverage_limits='Software fixture only'),
        'measurements':dict(id=IDENTITY,run_id=IDENTITY,published_id=None,observation_id='zero',configuration_identity='fixed',metric='latency',units='ms',value=0,status='recorded',reason=None,details={}),
        'assessments':dict(id=IDENTITY,claim_id=IDENTITY,campaign_id=None,supersedes_id=None,label='untested',tested_conditions={},evidence=[],justification='No experiment',limitations='Untested',reviewer=REVIEWER),
        'gaps':dict(id=IDENTITY,claim_id=IDENTITY,assessment_id=None,supersedes_id=None,status='open',reason='Missing test',next_check='Acquire new evidence',required_inputs=[],dependencies=[],feasibility='Unestablished',estimated_cost_usd=None,cost_basis='Unpriced',decision_rule='Frozen criterion',stopping_rule='Stop at bound',priority=1,campaign_id=None),
        'runs':dict(id=IDENTITY,campaign_id=IDENTITY,configuration_id=None,status='complete',reason=None,started_at='2026-01-01T00:00:00Z',ended_at='2026-01-01T00:00:01Z',analysis_version='fixture-v1',input_sha256=None,output_sha256=None,requested_resources={},actual_resources={},usage={},provenance={}),
        'summaries':dict(id=IDENTITY,run_id=IDENTITY,metric='latency',units='ms',statistics={},interval_kind='descriptive',uncertainty_method='Descriptive only',planned=1,unexpected=0,recorded=1,missing=0,invalid=0,ambiguous=0,excluded=0),
    }


class WorkflowValidationBoundaries(unittest.TestCase):
    def test_valid_records_preserve_zero_missing_values_and_do_not_mutate_nested_input(self):
        for kind, row in records().items():
            before = copy.deepcopy(row)
            self.assertEqual(workflow.validate_record(kind,row), before)
            self.assertEqual(row, before)
        with self.assertRaises(ValueError): workflow.finite(10 ** 5000, 'measurement')

    def test_public_paper_intake_requires_exact_input_identity_and_safe_filename(self):
        base = dict(title='Synthetic paper',domain='Networking',scope='Software fixture only',paper_url=None)
        upload = dict(sha256='a'*64,byte_count=1,filename='source.pdf')
        result = workflow.paper_details(dict(base,paper_upload=upload))
        self.assertEqual(result['paper_upload'],upload)
        for change in [{'sha256':'bad'}, {'byte_count':True}, {'byte_count':10000001}, {'filename':'../source.pdf'}, {'filename':'private\x00.pdf'}, {'filename':'source.txt'}]:
            with self.subTest(change=change), self.assertRaises(ValueError): workflow.paper_details(dict(base,paper_upload=dict(upload,**change)))
        for body in [dict(base,study_kind='unknown'),dict(base,study_kind='idea',paper_upload=upload),dict(base,paper_upload=upload,paper_url='https://example.test/paper')]:
            with self.assertRaises(ValueError): workflow.paper_details(body)
        with self.assertRaises(ValueError): workflow.reviewer(dict(REVIEWER,identity='private@example.test'))
        with self.assertRaises(ValueError): workflow.paper_details(dict(base,paper_url='http://example.test/paper'))
        for reviewer in [None,{},dict(REVIEWER,type='service')]:
            with self.assertRaises(ValueError):workflow.reviewer(reviewer)

    def test_origin_module_is_preserved_and_must_be_a_bounded_label(self):
        paper = dict(title='Synthetic paper',domain='Networking',scope='Software fixture only',paper_url='https://example.org/paper')
        self.assertEqual(workflow.paper_details(dict(paper,origin_module='synthetic-module'))['origin_module'],'synthetic-module')
        for invalid in ('', 'x'*101, 42):
            with self.subTest(origin_module=invalid), self.assertRaises(ValueError):
                workflow.paper_details(dict(paper,origin_module=invalid))

    def test_protocol_rejects_unknown_origin_undeclared_algorithms_duplicate_metrics_and_reversed_bounds(self):
        _raw, protocol, _campaign = fixture(); original=protocol['document']
        mutations = [dict(data_origin='invented'),dict(prior_exposure='Template must be amended before execution'),dict(questions=[]),dict(input_versions=[{'sha256':'bad'}]),dict(algorithms=[]),dict(controls=[]),dict(configurations=[{}]),
            dict(metrics=[None]),dict(resource_limits=[]),dict(seeds={'applicable':True,'rationale':''}),
            dict(configurations=original['configurations']*2),dict(metrics=original['metrics']*2),
            dict(metrics=[dict(original['metrics'][0],tolerance_absolute=-1)]),
            dict(metrics=[dict(original['metrics'][0],lower_bound=2,upper_bound=1)])]
        for change in mutations:
            with self.subTest(change=change),self.assertRaises(ValueError): workflow.validate_protocol(dict(original,**change))
        for key in ['max_observations','max_input_bytes','wall_seconds']:
            with self.assertRaises(ValueError): workflow.validate_protocol(dict(original,resource_limits=dict(original['resource_limits'],**{key:0})))

    def test_record_schema_and_domain_status_checks_reject_invalid_evidence(self):
        cases = {
            'sources':[{'review_status':'invented'},{'review_status':'complete-review'},{'access_status':'retrieved'}],
            'claims':[{'priority':0},{'claim_type':'invented'},{'scope':'private text'},{'priority':True}],
            'reviews':[{'scope':'unknown'},{'status':'unknown'}],
            'campaigns':[{'stage':'unknown'},{'experiment_type':'unknown'}],
            'measurements':[{'status':'invented'},{'value':None},{'status':'missing','value':1,'reason':'Missing'},{'status':'missing','value':None}],
            'assessments':[{'label':'invented'},{'label':'reproduced'},{'evidence':{}}],
            'gaps':[{'status':'invented'},{'estimated_cost_usd':-1}],
            'runs':[{'status':'failed'},{'ended_at':'2025-12-31T00:00:00Z'},{'ended_at':'2026-01-01T00:00:01'},{'input_sha256':'invalid'}],
            'summaries':[{'recorded':2},{'interval_kind':'invented'}],
        }
        originals=records()
        for kind, changes in cases.items():
            for change in changes:
                with self.subTest(kind=kind,change=change),self.assertRaises(ValueError): workflow.validate_record(kind,dict(originals[kind],**change))
        for kind, value in [('unknown',{}),('claims',None),('claims',dict(originals['claims'],private_field='secret'))]:
            with self.assertRaises(ValueError): workflow.validate_record(kind,value)
        for kind in originals:
            with self.subTest(kind=kind),self.assertRaises(ValueError):workflow.validate_record(kind,{'id':IDENTITY})

    def test_protocol_record_rejects_hash_and_registration_mismatch(self):
        _raw, protocol, _campaign=fixture()
        for change in [{'sha256':'a'*64},{'registration_kind':'unreviewed'}]:
            with self.assertRaises(ValueError): workflow.validate_record('protocols',dict(protocol,**change))


if __name__ == '__main__': unittest.main()
