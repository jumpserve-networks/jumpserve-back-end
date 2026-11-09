"""Frozen source arithmetic rejects ambiguous identities, units, and numeric loss."""
import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import source_arithmetic as arithmetic
from workflow import canonical,digest

ID='11111111-1111-4111-8111-111111111111'


def fixture(values=('1','2','1.5')):
    quotes=[label+' '+value for label,value in zip(['Baseline','Treatment','Reported'],values)];raw=canonical({'original_sha256':'a'*64,'pages':[{'page':1,'text':'\n'.join(quotes)}]})
    cells=[dict(id=identity,page=1,quote=quote,token_index=0,units='ms') for identity,quote in zip(['baseline','treatment','reported'],quotes)]
    return dict(source_id=ID,text_artifact_id=ID,text_sha256=digest(raw),cells=cells,calculations=[dict(id='mean',operation='mean',inputs=['baseline','treatment'],reported_cell='reported',decimal_places=1)]),raw


class SourceArithmeticBoundaries(unittest.TestCase):
    def test_schema_and_identity_bounds_reject_before_any_source_number_is_interpreted(self):
        base,_=fixture()
        for config in [None,[],{},dict(base,extra=True)]:
            with self.subTest(config=config),self.assertRaises(ValueError):arithmetic.validate(config)
        cases=[(('text_sha256',),'invalid'),(('cells',),None),(('cells',),[]),(('cells',),[{}]*101),(('cells',0),None),(('cells',0,'id'),'unsafe/path'),(('cells',0,'id'),0),(('cells',1,'id'),'baseline'),(('cells',0,'page'),True),(('cells',0,'page'),0),(('cells',0,'token_index'),True),(('cells',0,'token_index'),200),(('cells',0,'quote'),''),(('cells',0,'quote'),'x'*2001),(('cells',0,'units'),''),(('cells',0,'units'),'x'*101),(('calculations',),None),(('calculations',),[]),(('calculations',),[{}]*17),(('calculations',0),{}),(('calculations',0,'id'),'unsafe/path'),(('calculations',0,'inputs'),['baseline','baseline']),(('calculations',0,'decimal_places'),True),(('calculations',0,'decimal_places'),13),(('calculations',0,'reported_cell'),'absent')]
        for path,value in cases:
            changed=copy.deepcopy(base);target=changed
            for key in path[:-1]:target=target[key]
            target[path[-1]]=value
            with self.subTest(path=path,value=value),self.assertRaises(ValueError):arithmetic.validate(changed)
        duplicate=copy.deepcopy(base);duplicate['cells'][1].update(quote=duplicate['cells'][0]['quote'],token_index=0)
        with self.assertRaisesRegex(ValueError,'multiple cells'):arithmetic.validate(duplicate)
        duplicate=copy.deepcopy(base);duplicate['calculations'].append(copy.deepcopy(duplicate['calculations'][0]))
        with self.assertRaisesRegex(ValueError,'Unique calculation'):arithmetic.validate(duplicate)

    def test_operation_arity_and_reported_units_remain_part_of_the_frozen_contract(self):
        base,_=fixture()
        for change in [dict(operation='difference',inputs=['baseline','treatment','reported']),dict(operation='strict-increase',reported_cell='reported'),dict(operation='relative-percent-change')]:
            config=copy.deepcopy(base);config['calculations'][0].update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):arithmetic.validate(config)
        changed=copy.deepcopy(base);changed['cells'][1]['units']='seconds'
        with self.assertRaisesRegex(ValueError,'matching units'):arithmetic.validate(changed)

    def test_duplicate_pdf_page_identities_and_absent_declared_tokens_cannot_be_silently_resolved(self):
        config,raw=fixture();packet=__import__('json').loads(raw);packet['pages'].append(copy.deepcopy(packet['pages'][0]));duplicate=canonical(packet);config['text_sha256']=digest(duplicate)
        with self.assertRaisesRegex(ValueError,'Duplicate PDF page'):arithmetic.evaluate(config,duplicate,'a'*64)
        config,raw=fixture();config['cells'][0]['token_index']=1;rows=arithmetic.evaluate(config,raw,'a'*64);self.assertEqual(rows[0]['status'],'missing');self.assertIsNone(rows[0]['value']);self.assertIn('numeric token is absent',rows[0]['reason'])

    def test_decimal_overflow_and_lossy_float_conversion_remain_invalid_with_exact_source_tokens_retained(self):
        config,raw=fixture(('1e101','2','1.5'));rows=arithmetic.evaluate(config,raw,'a'*64);self.assertEqual(rows[0]['status'],'invalid');self.assertIsNone(rows[0]['value']);self.assertIn('Decimal precision',rows[0]['reason'])
        value='10000000000000000.1';config,raw=fixture((value,value,value));result=arithmetic.envelope(config,raw,'a'*64);observation=result['observations'][0];self.assertEqual(observation['status'],'invalid');self.assertIsNone(observation['value']);self.assertEqual(observation['metadata']['value'],value);self.assertIn('cannot be represented',observation['reason'])


if __name__=='__main__':unittest.main()
