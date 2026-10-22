"""Bounded evidence views; complete records remain outside the model context.

Pagination is over immutable, study-scoped records in a frozen time window.
Limits stop computation explicitly; they never return a complete-looking prefix.
"""
import hashlib
from workflow import canonical, digest, now

VERSION = 'autonomy-evidence-v2'
PAGE_SIZE = 500
MAX_RECORDS = 100000
MAX_BYTES = 32000000
READ_BYTES = 10000


def collect(reader, kind, study, **filters):
    boundary = now()
    result = []
    used = 0
    previous = None
    while True:
        params = dict(filters, order='id.asc', limit=PAGE_SIZE, created_at='lte.' + boundary)
        if previous is not None:
            params['and'] = '(id.gt.' + previous + ')'
        page = reader(kind, study, **params)
        if len(page) > PAGE_SIZE:
            raise ValueError('Evidence store exceeded its requested page size.')
        ids = [r['id'] for r in page]
        if ids != sorted(set(ids)) or previous is not None and ids and ids[0] <= previous:
            raise ValueError('Evidence pagination changed identity order; no partial finding was made.')
        used += len(canonical(page))
        result.extend(page)
        if len(result) > MAX_RECORDS or used > MAX_BYTES:
            raise ValueError('Server evidence calculation exceeded its declared resource bound; no data were truncated.')
        if not page:
            return result
        previous = ids[-1]


def manifest(records):
    # Hash the canonical stream without creating an additional full-data string.
    sha = hashlib.sha256(b'[')
    statuses = {}
    metrics = {}
    for index, row in enumerate(records):
        if index:
            sha.update(b',')
        sha.update(canonical(row))
        status = row.get('status', 'unspecified')
        statuses[status] = statuses.get(status, 0) + 1
        if 'metric' in row:
            key = (row['metric'], row.get('units'))
            metrics[key] = metrics.get(key, 0) + 1
    sha.update(b']')
    return dict(record_count=len(records), records_sha256=sha.hexdigest(), status_counts=statuses,
                metric_counts=[dict(metric=k[0], units=k[1], count=v) for k, v in sorted(metrics.items())],
                scope='Complete retained record inventory, not a statistical estimate or source review.')


def page(records, offset, byte_budget=None):
    byte_budget = READ_BYTES if byte_budget is None else byte_budget
    if type(byte_budget) is not int or not 1 <= byte_budget <= READ_BYTES:
        raise ValueError('Evidence page byte budget is outside the reader bound.')
    if type(offset) is not int or offset < 0 or offset > len(records):
        raise ValueError('Evidence cursor is outside the authorized record set.')
    def envelope(selected):
        end = offset + len(selected)
        return dict(records=selected, offset=offset, next_cursor=end if end < len(records) else None,
                    total_records=len(records), returned_records=len(selected),
                    page_sha256=digest(canonical(selected)), complete=end == len(records) and offset == 0,
                    limitation='A page is an explicit subset. Full-data calculations do not sample this page.')
    selected = []
    for row in records[offset:offset + 20]:
        if len(canonical(envelope(selected + [row]))) > byte_budget:
            if not selected:
                raise ValueError('This evidence row exceeds the bounded reader; use its original private artifact.')
            break
        selected.append(row)
    return envelope(selected)


def document_page(document, cursor):
    # Unicode character cursors avoid splitting UTF-8. The full canonical byte
    # hash binds every fragment; fragments are explicitly not standalone JSON.
    text = canonical(document).decode()
    if type(cursor) is not int or not 0 <= cursor <= len(text):
        raise ValueError('Protocol cursor is outside the authorized document.')
    part = text[cursor:cursor + 1500]
    end = cursor + len(part)
    return dict(fragment=part, cursor=cursor, next_cursor=end if end < len(text) else None,
                total_characters=len(text), document_sha256=digest(text.encode()),
                fragment_sha256=digest(part.encode()), representation='canonical-json-character-fragment-v1')
