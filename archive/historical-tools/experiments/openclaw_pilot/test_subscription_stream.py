"""Exposed stream reconstruction checks; no provider dispatch."""
import copy
import io
import json
from pathlib import Path
import sys
import time
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parent))
import subscription as sub
import subscription_stream as parser
from test_subscription import completed,sse


def events(text='{}'):
    item=dict(id='synthetic-item',type='message',role='assistant',status='completed',content=[dict(type='output_text',text=text)])
    value=completed();value['output']=[]
    return [dict(type='response.output_item.added',output_index=0,item=dict(item,status='in_progress',content=[])),
        dict(type='response.output_text.delta',output_index=0,item_id=item['id'],content_index=0,delta=text),
        dict(type='response.output_text.done',output_index=0,item_id=item['id'],content_index=0,text=text),
        dict(type='response.output_item.done',output_index=0,item=item),dict(type='response.completed',response=value)]


class Stream(unittest.TestCase):
    def test_empty_terminal_output_uses_verified_completed_item_and_preserves_original_bytes(self):
        raw=sse(*events());sink=io.BytesIO()
        value=parser.read_stream(io.BytesIO(raw),sink,time.monotonic()+10,1048576)
        self.assertEqual(value['output'][0]['content'][0]['text'],'{}');self.assertEqual(sink.getvalue(),raw)

    def test_conflicting_text_duplicate_and_unmatched_identities_are_rejected(self):
        for mode in ('text','identity','duplicate','index'):
            value=events()
            if mode=='text':value[1]['delta']='different'
            if mode=='identity':value[3]['item']['id']='other'
            if mode=='duplicate':value.insert(4,copy.deepcopy(value[3]))
            if mode=='index':value[3]['output_index']=2
            with self.subTest(mode=mode),self.assertRaises(sub.StreamFailure):
                parser.read_stream(io.BytesIO(sse(*value)),io.BytesIO(),time.monotonic()+10,1048576)

    def test_items_without_terminal_completion_are_not_success(self):
        with self.assertRaises(sub.StreamFailure):
            parser.read_stream(io.BytesIO(sse(*events()[:-1])),io.BytesIO(),time.monotonic()+10,1048576)

    def test_actual_retained_stream_reanalysis_is_not_a_new_model_call(self):
        path=Path(__file__).with_name('artifacts') / 'v8/openclaw-runtime/provider-02/response.bin'
        if not path.exists():self.skipTest('Private retained actual stream unavailable; synthetic regressions still apply')
        raw=path.read_bytes();self.assertEqual(sub.lab.sha(raw),'4f8223fdd0ce705119c837d57940489cf34ec3058861a5f1f4e1891dd8ce3025')
        value=parser.read_stream(io.BytesIO(raw),io.BytesIO(),time.monotonic()+10,1048576)
        answer=sub.structured_response(value)
        self.assertEqual(sub.lab.model.parse(answer)['action'],'choose-check')
        self.assertEqual(answer['usage']['inputTokens'],23710)


if __name__=='__main__':unittest.main()
