"""Versioned reconstruction of completed streamed items, retaining original SSE."""
import copy
import json
import subscription as sub

ORIGINAL_READ=sub.read_stream
VERSION='siwc-streamed-output-items-v1'


def read_stream(response,sink,deadline,maximum):
    retained=bytearray()
    class Tee:
        def write(self,raw):retained.extend(raw);return sink.write(raw)
        def flush(self):sink.flush()
    terminal=ORIGINAL_READ(response,Tee(),deadline,maximum)
    items={};texts={};deltas={};ids={}
    for block in bytes(retained).replace(b'\r\n',b'\n').split(b'\n\n'):
        lines=[line[5:].lstrip() for line in block.split(b'\n') if line.startswith(b'data:')]
        if not lines or b'\n'.join(lines)==b'[DONE]':continue
        event=json.loads(b'\n'.join(lines));kind=event.get('type')
        if kind not in ('response.output_item.added','response.output_item.done','response.output_text.delta','response.output_text.done'):continue
        index=event.get('output_index')
        if type(index) is not int or index<0:raise sub.StreamFailure('ambiguous-stream-output-index')
        if kind in ('response.output_item.added','response.output_item.done'):
            item=event.get('item') or {};identity=item.get('id')
            if not isinstance(identity,str) or not identity or index in ids and ids[index]!=identity:
                raise sub.StreamFailure('ambiguous-stream-output-identity')
            ids[index]=identity
            if kind=='response.output_item.done':
                if index in items or item.get('status') not in (None,'completed'):
                    raise sub.StreamFailure('duplicate-or-incomplete-stream-item')
                items[index]=item
        else:
            key=(index,event.get('item_id'),event.get('content_index'))
            if type(key[2]) is not int or key[2]<0 or not isinstance(key[1],str):
                raise sub.StreamFailure('ambiguous-stream-content-identity')
            if kind=='response.output_text.delta':
                if not isinstance(event.get('delta'),str):raise sub.StreamFailure('invalid-stream-text-delta')
                deltas[key]=deltas.get(key,'')+event['delta']
                if len(deltas[key].encode())>100000:raise sub.StreamFailure('stream-text-byte-limit')
            else:
                if key in texts or not isinstance(event.get('text'),str):raise sub.StreamFailure('ambiguous-final-stream-text')
                texts[key]=event['text']
                if key in deltas and deltas[key]!=texts[key]:raise sub.StreamFailure('stream-delta-final-text-disagreement')
    ordered=[items[index] for index in sorted(items)]
    if ordered and sorted(items)!=list(range(len(items))):raise sub.StreamFailure('missing-stream-output-item')
    for index,item in items.items():
        for content_index,content in enumerate(item.get('content',[])):
            if content.get('type')!='output_text':continue
            key=(index,item['id'],content_index)
            if key in texts and texts[key]!=content.get('text') or key in deltas and deltas[key]!=content.get('text'):
                raise sub.StreamFailure('stream-item-text-disagreement')
    for key in texts:
        if key[0] not in items or items[key[0]]['id']!=key[1]:raise sub.StreamFailure('unmatched-final-stream-text')
    if terminal.get('output'):
        if ordered and terminal['output']!=ordered:raise sub.StreamFailure('terminal-stream-output-disagreement')
        return terminal
    if not ordered:raise sub.StreamFailure('missing-completed-stream-items')
    value=copy.deepcopy(terminal);value['output']=ordered
    return value
