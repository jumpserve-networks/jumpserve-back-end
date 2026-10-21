"""SIWC wire protocol. Derived from preserved pilot v7-v9; no credential access.

Original production adapter is separate from frozen pilot revisions. Includes the
explicit JSON developer input and terminal/output-item consistency correction.
"""
import copy,json,time
import autonomy_model as model
from workflow import canonical
MODEL='gpt-6.1-sol'
LIMIT_CODES={'subscription_sharing_usage_limit_exceeded','subscription_sharing_usage_unavailable','subscription_sharing_user_unavailable','rate_limit_exceeded','insufficient_quota'}
AUTH_CODES={'subscription_sharing_user_not_eligible','subscription_sharing_invalid_user','chatpass_v2_scope_not_authorized','chatpass_v2_invalid_authorization_context'}

def response_request(request):
    """Logical Bedrock-shaped controller data -> SIWC-compatible text request."""
    if request['modelId'] != MODEL:
        raise ValueError('Unexpected logical model')
    schema = request['toolConfig']['tools'][0]['toolSpec']['inputSchema']['json']
    instructions = '\n\n'.join(item['text'] for item in request['system'])
    instructions += '\nReturn exactly one JSON object satisfying this decision schema. No tools are available.\n' + canonical(schema).decode()
    messages = [dict(role='developer',content='Return exactly one JSON object satisfying the supplied decision schema.')]
    for message in request['messages']:
        if message['role'] not in ('user', 'assistant') or any(set(c) != {'text'} for c in message['content']):
            raise ValueError('Only text user/assistant messages are accepted')
        messages.append(dict(role=message['role'], content='\n'.join(c['text'] for c in message['content'])))
    return dict(model=MODEL, instructions=instructions, input=messages, store=False, stream=True,
                reasoning=dict(effort='medium'), text=dict(format=dict(type='json_object')), tools=[])

def usage(value):
    original = value.get('usage') if isinstance(value, dict) else None
    if not isinstance(original, dict):
        return None
    fields = {'inputTokens': 'input_tokens', 'outputTokens': 'output_tokens', 'totalTokens': 'total_tokens'}
    return {out: original.get(key) if type(original.get(key)) is int and original[key] >= 0 else None
            for out, key in fields.items()}

class StreamFailure(ValueError):
    def __init__(self, code, status='unknown', observed_usage=None):
        super().__init__(code)
        self.code, self.status, self.usage = code, status, observed_usage

def failure(code, status=None, observed_usage=None):
    state = 'paused-quota' if code in LIMIT_CODES or status == 429 else (
            'paused-auth' if code in AUTH_CODES or status in (401, 403) else 'failed')
    return StreamFailure(code or 'provider-error', state, observed_usage)

def read_terminal_stream(response, sink, deadline, maximum):
    """Persist every received byte before parsing. Completion requires the terminal event."""
    buffer = b''
    frame = []
    size = 0
    completed = None
    observed_usage = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise StreamFailure('provider-wall-limit', observed_usage=observed_usage)
        connection = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
        if connection is not None:
            connection.settimeout(min(30, remaining))
        chunk = response.read1(min(4096, maximum - size + 1))
        if not chunk:
            break
        sink.write(chunk); sink.flush()
        size += len(chunk)
        if size > maximum:
            raise StreamFailure('provider-response-byte-limit', observed_usage=observed_usage)
        buffer += chunk
        while b'\n' in buffer:
            line, buffer = buffer.split(b'\n', 1)
            line = line.rstrip(b'\r')
            if line:
                if line.startswith(b'data:'):
                    frame.append(line[5:].lstrip())
                continue
            if not frame:
                continue
            payload = b'\n'.join(frame); frame = []
            if payload == b'[DONE]':
                continue
            try:
                event = json.loads(payload)
            except (ValueError, UnicodeError):
                raise StreamFailure('malformed-provider-stream', observed_usage=observed_usage)
            kind = event.get('type')
            value = event.get('response') or {}
            observed_usage = usage(value) or observed_usage
            if kind in ('response.failed', 'response.incomplete', 'error'):
                error = value.get('error') or event.get('error') or event
                code = error.get('code') or kind
                raise failure(code, observed_usage=observed_usage) if kind != 'response.incomplete' else StreamFailure(code, observed_usage=observed_usage)
            if kind == 'response.completed':
                if completed is not None or value.get('status') != 'completed':
                    raise StreamFailure('invalid-terminal-response', observed_usage=observed_usage)
                completed = value
    if buffer or frame or completed is None:
        raise StreamFailure('incomplete-provider-stream', observed_usage=observed_usage)
    return completed

def structured_response(value):
    if value.get('model') != MODEL:
        raise ValueError('Provider reported a different model; no silent substitution')
    texts = []
    for item in value.get('output', []):
        if item.get('type') == 'reasoning':
            continue
        if item.get('type') != 'message' or item.get('role') != 'assistant':
            raise ValueError('Provider tool calls or unexpected output are prohibited')
        for content in item.get('content', []):
            if content.get('type') != 'output_text':
                raise ValueError('Provider refusal or non-text output')
            texts.append(content['text'])
    if len(texts) != 1 or len(texts[0].encode()) > 100000:
        raise ValueError('Expected one bounded JSON decision')
    decision = json.loads(texts[0])
    response = dict(stopReason='tool_use', output=dict(message=dict(role='assistant', content=[dict(toolUse=dict(
        name='submit_assessment_step', toolUseId='subscription-data-only', input=decision))])))
    response['usage'] = usage(value) or {}
    model.parse(response)
    return response

def read_stream(response,sink,deadline,maximum):
    retained=bytearray()
    class Tee:
        def write(self,raw):retained.extend(raw);return sink.write(raw)
        def flush(self):sink.flush()
    terminal=read_terminal_stream(response,Tee(),deadline,maximum)
    items={};texts={};deltas={};ids={}
    for block in bytes(retained).replace(b'\r\n',b'\n').split(b'\n\n'):
        lines=[line[5:].lstrip() for line in block.split(b'\n') if line.startswith(b'data:')]
        if not lines or b'\n'.join(lines)==b'[DONE]':continue
        event=json.loads(b'\n'.join(lines));kind=event.get('type')
        if kind not in ('response.output_item.added','response.output_item.done','response.output_text.delta','response.output_text.done'):continue
        index=event.get('output_index')
        if type(index) is not int or index<0:raise StreamFailure('ambiguous-stream-output-index')
        if kind in ('response.output_item.added','response.output_item.done'):
            item=event.get('item') or {};identity=item.get('id')
            if not isinstance(identity,str) or not identity or index in ids and ids[index]!=identity:
                raise StreamFailure('ambiguous-stream-output-identity')
            ids[index]=identity
            if kind=='response.output_item.done':
                if index in items or item.get('status') not in (None,'completed'):
                    raise StreamFailure('duplicate-or-incomplete-stream-item')
                items[index]=item
        else:
            key=(index,event.get('item_id'),event.get('content_index'))
            if type(key[2]) is not int or key[2]<0 or not isinstance(key[1],str):
                raise StreamFailure('ambiguous-stream-content-identity')
            if kind=='response.output_text.delta':
                if not isinstance(event.get('delta'),str):raise StreamFailure('invalid-stream-text-delta')
                deltas[key]=deltas.get(key,'')+event['delta']
                if len(deltas[key].encode())>100000:raise StreamFailure('stream-text-byte-limit')
            else:
                if key in texts or not isinstance(event.get('text'),str):raise StreamFailure('ambiguous-final-stream-text')
                texts[key]=event['text']
                if key in deltas and deltas[key]!=texts[key]:raise StreamFailure('stream-delta-final-text-disagreement')
    ordered=[items[index] for index in sorted(items)]
    if ordered and sorted(items)!=list(range(len(items))):raise StreamFailure('missing-stream-output-item')
    for index,item in items.items():
        for content_index,content in enumerate(item.get('content',[])):
            if content.get('type')!='output_text':continue
            key=(index,item['id'],content_index)
            if key in texts and texts[key]!=content.get('text') or key in deltas and deltas[key]!=content.get('text'):
                raise StreamFailure('stream-item-text-disagreement')
    for key in texts:
        if key[0] not in items or items[key[0]]['id']!=key[1]:raise StreamFailure('unmatched-final-stream-text')
    if terminal.get('output'):
        if ordered and terminal['output']!=ordered:raise StreamFailure('terminal-stream-output-disagreement')
        return terminal
    if not ordered:raise StreamFailure('missing-completed-stream-items')
    value=copy.deepcopy(terminal);value['output']=ordered
    return value
