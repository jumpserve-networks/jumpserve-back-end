"""Source-bound deterministic calculations. No eval, code, or model observations.

Cells identify one exact quote on one retained PDF-text page and a zero-based
numeric token. Repeated quotes are ambiguous, never silently picked. Results
establish consistency of source numbers, not empirical truth or causality.
"""
from decimal import Decimal, InvalidOperation, localcontext, ROUND_HALF_EVEN
import json
import re
from workflow import canonical, digest, identifier

VERSION = 'source-arithmetic-v1'
TOKEN = re.compile(r'(?<![\w.])[+\-−]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?(?![\w]|\.\d|,\d{3})')
OPS = ('mean', 'difference', 'relative-percent-change', 'strict-increase')

def decimal_token(lexeme):
    return Decimal(lexeme.replace('−','-').replace(',',''))


def validate(config):
    if not isinstance(config, dict) or set(config) != {'source_id', 'text_artifact_id', 'text_sha256', 'cells', 'calculations'}:
        raise ValueError('Arithmetic needs an exact source/text artifact, cells and calculations.')
    identifier(config['source_id']); identifier(config['text_artifact_id'])
    if not re.fullmatch('[a-f0-9]{64}', str(config['text_sha256'])): raise ValueError('Exact extracted-text hash required.')
    cells = config['cells']; calculations = config['calculations']
    if not isinstance(cells, list) or not 1 <= len(cells) <= 100: raise ValueError('Declare 1..100 source cells.')
    ids = set(); coordinates = set()
    for cell in cells:
        if not isinstance(cell, dict) or set(cell) != {'id', 'page', 'quote', 'token_index', 'units'}: raise ValueError('Source cell identity is incomplete.')
        if not isinstance(cell['id'], str) or not re.fullmatch('[a-z][a-z0-9_-]{0,49}', cell['id']) or cell['id'] in ids: raise ValueError('Unique safe cell IDs required.')
        ids.add(cell['id'])
        if type(cell['page']) is not int or cell['page'] < 1 or type(cell['token_index']) is not int or not 0 <= cell['token_index'] < 200: raise ValueError('Invalid source page/token identity.')
        if not isinstance(cell['quote'], str) or not 1 <= len(cell['quote']) <= 2000 or not isinstance(cell['units'], str) or not 1 <= len(cell['units']) <= 100: raise ValueError('Bounded exact quote and units required.')
        coordinate=(cell['page'],cell['quote'],cell['token_index'])
        if coordinate in coordinates: raise ValueError('One source token cannot masquerade as multiple cells.')
        coordinates.add(coordinate)
    if not isinstance(calculations, list) or not 1 <= len(calculations) <= 16: raise ValueError('Declare 1..16 calculations.')
    seen = set(); by_id = {c['id']: c for c in cells}
    for calc in calculations:
        if not isinstance(calc, dict) or set(calc) != {'id', 'operation', 'inputs', 'reported_cell', 'decimal_places'}: raise ValueError('Calculation schema is incomplete.')
        if not isinstance(calc['id'], str) or not re.fullmatch('[a-z][a-z0-9_-]{0,49}', calc['id']) or calc['id'] in seen: raise ValueError('Unique calculation IDs required.')
        seen.add(calc['id'])
        op = calc['operation']; inputs = calc['inputs']
        if op not in OPS or not isinstance(inputs, list) or not 2 <= len(inputs) <= 100 or any(i not in ids for i in inputs): raise ValueError('Unknown operation or input cell.')
        if len(set(inputs))!=len(inputs): raise ValueError('Calculation input identities cannot be repeated.')
        if op in ('difference', 'relative-percent-change') and len(inputs) != 2: raise ValueError('Binary operation requires two ordered inputs: baseline then treatment.')
        if len({by_id[i]['units'] for i in inputs}) != 1: raise ValueError('Arithmetic inputs must have matching units.')
        if type(calc['decimal_places']) is not int or not 0 <= calc['decimal_places'] <= 12: raise ValueError('Declare 0..12 decimal places, ROUND_HALF_EVEN.')
        target = calc['reported_cell']
        if op == 'strict-increase':
            if target is not None: raise ValueError('Strict increase uses the declared criterion, not an invented published numeric cell.')
        elif target not in ids or by_id[target]['units'] != ('%' if op == 'relative-percent-change' else by_id[inputs[0]]['units']): raise ValueError('A matching-unit reported result cell is required.')
    return config


def evaluate(config, raw, original_pdf_sha256):
    validate(config)
    if digest(raw) != config['text_sha256']: raise ValueError('Original extracted text hash differs.')
    packet = json.loads(raw)
    if packet.get('original_sha256') != original_pdf_sha256: raise ValueError('Extracted text is not bound to the claim source PDF.')
    pages = {}
    for page in packet['pages']:
        n = page.get('page')
        if n in pages: raise ValueError('Duplicate PDF page identity.')
        pages[n] = page.get('text', '')
    values = {}; cells = {}
    for cell in config['cells']:
        page = pages.get(cell['page'], ''); quote = cell['quote']
        status = 'recorded'; reason = None; value = None; lexeme = None
        if page.count(quote) != 1:
            status = 'ambiguous' if page.count(quote) > 1 else 'missing'; reason = 'Source quote is repeated.' if status == 'ambiguous' else 'Exact source quote not found on the declared page.'
        else:
            tokens = TOKEN.findall(quote)
            if cell['token_index'] >= len(tokens): status = 'missing'; reason = 'Declared numeric token is absent.'
            else:
                lexeme = tokens[cell['token_index']]
                try:
                    value = decimal_token(lexeme)
                    if not value.is_finite() or len(value.as_tuple().digits) > 100 or abs(value.adjusted()) > 100: raise InvalidOperation()
                except InvalidOperation: status = 'invalid'; value = None; reason = 'Numeric token exceeds bounded Decimal precision.'
        values[cell['id']] = value
        cells[cell['id']] = dict(cell, status=status, reason=reason, original_numeric_lexeme=lexeme)
    rows = []
    for calc in config['calculations']:
        relevant = calc['inputs'] + ([calc['reported_cell']] if calc['reported_cell'] else [])
        bad = [cells[i] for i in relevant if cells[i]['status'] != 'recorded']
        status = bad[0]['status'] if bad else 'recorded'; reason = bad[0]['reason'] if bad else None
        value = None; reported = None; matched = None
        if not bad:
            try:
                with localcontext() as ctx:
                    ctx.prec = 250
                    ctx.rounding = ROUND_HALF_EVEN
                    v = [values[i] for i in calc['inputs']]; op = calc['operation']
                    if op == 'mean': value = sum(v) / len(v)
                    elif op == 'difference': value = v[1] - v[0]
                    elif op == 'relative-percent-change': value = 100 * (v[1] - v[0]) / v[0]
                    else: value = Decimal(int(all(a < b for a, b in zip(v, v[1:]))))
                    unit = Decimal(1).scaleb(-calc['decimal_places'])
                    value = value.quantize(unit)
                    reported = Decimal(1) if op == 'strict-increase' else values[calc['reported_cell']].quantize(unit)
                    matched = value == reported
            except (InvalidOperation, ArithmeticError): status = 'invalid'; value = None; reason = 'Undefined arithmetic (including zero baseline) or precision budget exceeded.'
        rows.append(dict(id=calc['id'], value=str(value) if value is not None else None, reported=str(reported) if reported is not None else None,
                         status=status, reason=reason, matched=matched, operation=calc['operation'], cells=[cells[i] for i in relevant],
                         units='%' if calc['operation'] == 'relative-percent-change' else 'boolean' if calc['operation'] == 'strict-increase' else cells[calc['inputs'][0]]['units']))
    return rows


def envelope(config, raw, source_sha256):
    rows = evaluate(config, raw, source_sha256)
    observations = []
    for row in rows:
        value = float(row['value']) if row['value'] is not None else None
        if value is not None and Decimal(str(value)) != Decimal(row['value']):
            row = dict(row, status='invalid', reason='Decimal result cannot be represented by the measurement number; exact string retained.'); value = None
        observations.append(dict(observation_key=row['id'], metric=row['id'], units=row['units'], value=value, status=row['status'], reason=row['reason'], metadata=row))
    return dict(version='network-observations-v1', observations=observations,
                provenance=dict(runner=VERSION, text_sha256=digest(raw), original_pdf_sha256=source_sha256,
                                implementation_sha256=digest(__import__('pathlib').Path(__file__).read_bytes()),
                                scope='Source numerical consistency only; no new empirical measurements or causal validation.', rounding='ROUND_HALF_EVEN at frozen decimal_places'), usage=None)


def contract():
    return dict(version=VERSION, executor_configured=True, operations=list(OPS), limits=dict(cells=100, calculations=16),
                config_shape=dict(source_id='Claim source UUID', text_artifact_id='Retained extracted-text artifact UUID', text_sha256='Exact original extracted-text SHA256',
                                  cells=[dict(id='cell_id', page='1-based PDF page', quote='Exact unique source quote', token_index='Zero-based numeric token within quote', units='Original units')],
                                  calculations=[dict(id='metric_name', operation=list(OPS), inputs=['baseline_cell', 'treatment_cell'], reported_cell='Source cell for reported answer; null only for strict-increase', decimal_places='Integer 0..12')]),
                rules=['Read the original source text before selecting tokens. Never supply observed values.', 'Declare each calculation id as a metric, its actual units, planned_observations=1.',
                       'Use data_origin=archived-data and experiment_type=independent-check. This checks source arithmetic only. It cannot resolve an empirical, causal or generalization claim.',
                       'difference and relative-percent-change BOTH use inputs=[baseline,treatment]. difference=treatment-baseline; relative-percent-change=100*(treatment-baseline)/baseline. Do not use ordinary left-minus-right order for difference.',
                       'New autonomous decisions require a consistent baseline/treatment order for the same source-cell pair within one configuration. Intentionally different contrasts need separate justified configurations.',
                       'Fresh reviewers must compare ordered operand identities and source values against the claimed calculation; a wrong expression is an implementation/configuration error, not a scientific discrepancy.',
                       'Both repeated/missing quotes and undefined arithmetic remain unavailable, never zero.'])


def preview(config, raw, source_sha256):
    """Explicit expression order for method review; never alters frozen inputs."""
    rows = evaluate(config, raw, source_sha256)
    result = []
    for calc, row in zip(config['calculations'], rows):
        ids = calc['inputs']; op = calc['operation']
        expression = (ids[1]+' - '+ids[0] if op == 'difference' else
                      '100 * ('+ids[1]+' - '+ids[0]+') / '+ids[0] if op == 'relative-percent-change' else
                      ' < '.join(ids) if op == 'strict-increase' else 'mean('+', '.join(ids)+')')
        result.append(dict(id=calc['id'], expression=expression, ordered_inputs=ids,
            operands=[dict(id=c['id'], value=c['original_numeric_lexeme'], units=c['units'], status=c['status']) for c in row['cells'][:len(ids)]],
            value=row['value'], reported=row['reported'], matched=row['matched'], status=row['status'], units=row['units']))
    return dict(scope='Design preview from frozen source text. Review expression intent against the claim. Shares the source parser and arithmetic implementation; not independent evidence, empirical validation or an authorization to change original results.', calculations=result)

def schema():
    def obj(properties): return dict(type='object',additionalProperties=False,required=list(properties),properties=properties)
    string=dict(type='string',minLength=1,maxLength=100)
    cell=obj(dict(id=string,page=dict(type='integer',minimum=1),quote=dict(type='string',minLength=1,maxLength=2000),token_index=dict(type='integer',minimum=0,maximum=199),units=string))
    calculation=obj(dict(id=string,operation=dict(type='string',enum=list(OPS)),inputs=dict(type='array',minItems=2,maxItems=100,items=string),reported_cell=dict(type=['string','null']),decimal_places=dict(type='integer',minimum=0,maximum=12)))
    return obj(dict(source_id=dict(type='string',format='uuid'),text_artifact_id=dict(type='string',format='uuid'),text_sha256=dict(type='string',pattern='^[a-f0-9]{64}$'),cells=dict(type='array',minItems=1,maxItems=100,items=cell),calculations=dict(type='array',minItems=1,maxItems=16,items=calculation)))
